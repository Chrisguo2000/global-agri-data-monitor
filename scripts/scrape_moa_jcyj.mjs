import fs from "node:fs/promises";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const BASE = process.env.MOA_JCYJ_URL || "https://xmsyj.moa.gov.cn/jcyj/";
const outDirArg = process.argv.find((arg) => arg.startsWith("--out-dir="))?.split("=").slice(1).join("=");
const OUT_DIR = path.resolve(outDirArg || process.env.OUT_DIR || "dist");
const RAW_DIR = path.join(OUT_DIR, "raw_html");
const DATA_JSON = path.join(OUT_DIR, "moa_jcyj_data.json");
const execFileAsync = promisify(execFile);

const pagesArg = process.argv.find((arg) => arg.startsWith("--pages="))?.split("=").slice(1).join("=");
const PAGES_TO_FETCH = pagesArg || process.env.PAGES_TO_FETCH || "1";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function decodeHtml(input = "") {
  return input
    .replace(/&nbsp;/g, " ")
    .replace(/&ensp;/g, " ")
    .replace(/&emsp;/g, " ")
    .replace(/&ldquo;|&rdquo;/g, '"')
    .replace(/&lsquo;|&rsquo;/g, "'")
    .replace(/&mdash;/g, "-")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, n) => String.fromCharCode(Number.parseInt(n, 16)));
}

function cleanText(input = "") {
  return decodeHtml(input)
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>/gi, "\n")
    .replace(/<\/div>/gi, "\n")
    .replace(/<\/li>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t\r\f\v]+/g, " ")
    .replace(/\n\s+/g, "\n")
    .replace(/\s+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function normalizeSpacing(input = "") {
  return cleanText(input)
    .replace(/\s*([，。；：、（）%])\s*/g, "$1")
    .replace(/\s+([年月日周])/g, "$1")
    .replace(/([第月])\s+/g, "$1")
    .replace(/(\d)\s+(\d)/g, "$1$2")
    .replace(/\s+/g, " ")
    .trim();
}

function pageUrl(pageIndex) {
  if (pageIndex === 0) return BASE;
  return new URL(`index_${pageIndex}.htm`, BASE).href;
}

async function fetchText(url, tries = 3) {
  let lastError;
  for (let attempt = 1; attempt <= tries; attempt += 1) {
    try {
      const { stdout } = await execFileAsync("curl", [
        "-L",
        "-sS",
        "--fail",
        "--retry",
        "2",
        "--connect-timeout",
        "15",
        "--max-time",
        "45",
        "-A",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36",
        url,
      ], { encoding: "utf8", maxBuffer: 8 * 1024 * 1024 });
      return stdout;
    } catch (error) {
      lastError = error;
      await sleep(400 * attempt);
    }
  }
  throw new Error(`Fetch failed for ${url}: ${lastError?.message || lastError}`);
}

function extractCountPage(html) {
  const match = html.match(/var\s+countPage\s*=\s*(\d+)/);
  return match ? Number(match[1]) : 1;
}

function extractListItems(html, listUrl) {
  const items = [];
  const listBlock = html.match(/<ul\s+id=["']div["'][^>]*>([\s\S]*?)<\/ul>/i)?.[1] || html;
  const itemRe = /<li\b[\s\S]*?<a\s+href=["']([^"']+)["'][^>]*title=['"]([^'"]+)['"][\s\S]*?<span[^>]*class=["']sj_gztzri["'][^>]*>([\s\S]*?)<\/span>[\s\S]*?<\/a>[\s\S]*?<\/li>/gi;
  let match;
  while ((match = itemRe.exec(listBlock))) {
    const url = new URL(decodeHtml(match[1]), listUrl).href;
    const title = normalizeSpacing(match[2]);
    const publishDate = normalizeSpacing(match[3]);
    items.push({ title, publishDate, url });
  }
  return items;
}

function extractArticle(html, url, fallback = {}) {
  const h1 = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)?.[1];
  const title = normalizeSpacing(h1 || fallback.title || "");
  const dateLine = normalizeSpacing(html.match(/日期：([\s\S]*?)打印本页/i)?.[1] || "");
  const dateMatch = dateLine.match(/(\d{4}-\d{2}-\d{2})/);
  const sourceMatch = dateLine.match(/来源：([^〖]+?)(?:\s|$)/);
  const authorMatch = dateLine.match(/作者：([^来]*?)\s*来源：/);

  const bodyBlock =
    html.match(/<div[^>]+class=["'][^"']*(?:TRS_Editor|arc_body|article|content)[^"']*["'][^>]*>([\s\S]*?)(?:<div\s+class=["']fj|<div\s+class=["']footer|<div class="sj_er_kuang|<\/body>)/i)?.[1] ||
    html.match(/日期：[\s\S]*?打印本页([\s\S]*?)<div class="sj_er_kuang/i)?.[1] ||
    "";

  const paragraphs = cleanText(bodyBlock)
    .split(/\n+/)
    .map((p) => normalizeSpacing(p))
    .filter(Boolean)
    .filter((p) => !/^机关子站|^直属单位网站|^国务院各部门网站|^地方农业管理部门网站/.test(p));

  const text = paragraphs.join("\n");
  const type = title.includes("定点屠宰")
    ? "生猪定点屠宰价格"
    : title.includes("畜产品和饲料")
      ? "畜产品和饲料集贸市场价格"
      : "其他监测预警";

  const tableCount = (bodyBlock.match(/<table\b/gi) || []).length;
  const attachments = [...html.matchAll(/<a\s+href=["']([^"']+\.(?:xls|xlsx|csv|doc|docx|pdf|zip))["'][^>]*>([\s\S]*?)<\/a>/gi)].map((m) => ({
    text: normalizeSpacing(m[2]),
    url: new URL(decodeHtml(m[1]), url).href,
  }));

  return {
    ...fallback,
    title,
    publishDate: dateMatch?.[1] || fallback.publishDate || "",
    source: sourceMatch?.[1]?.trim() || "",
    author: authorMatch?.[1]?.trim() || "",
    type,
    url,
    paragraphCount: paragraphs.length,
    text,
    paragraphs,
    tableCount,
    attachments,
  };
}

function extractPeriod(article) {
  const full = `${article.title} ${article.text}`;
  const titlePeriod = article.title.match(/(\d+月第\d+周)/)?.[1] || "";
  const collectionDate = full.match(/采集日为\s*(\d+月\d+日)/)?.[1] || "";
  return { period: titlePeriod, collectionDate };
}

function normalizeDirection(direction = "") {
  if (direction.includes("上涨")) return "上涨";
  if (direction.includes("下跌")) return "下跌";
  if (direction.includes("持平") || direction.includes("相同")) return "持平";
  return direction;
}

function signedPct(direction, value) {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  const dir = normalizeDirection(direction);
  if (dir === "下跌") return -Math.abs(value);
  if (dir === "上涨") return Math.abs(value);
  if (dir === "持平") return 0;
  return value;
}

function normalizeNumber(raw) {
  if (!raw) return null;
  const value = Number(String(raw).replace(/\s+/g, ""));
  return Number.isFinite(value) ? value : null;
}

function addMetric(rows, article, data) {
  const { period, collectionDate } = extractPeriod(article);
  rows.push({
    publishDate: article.publishDate,
    period,
    collectionDate,
    articleType: article.type,
    title: article.title,
    url: article.url,
    ...data,
  });
}

function sentenceSplit(text) {
  return text
    .replace(/\n/g, " ")
    .split(/(?<=[。；])/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function extractMarketMetrics(article) {
  const rows = [];
  if (article.type !== "畜产品和饲料集贸市场价格") return rows;

  for (const sentence of sentenceSplit(article.text)) {
    const categoryMatch = sentence.match(/^([^。；]*?价格)。/);
    const category = categoryMatch?.[1] || "";

    const avgRe = /(?:(全国|主产区东北三省|主销区广东省|(?:河北、内蒙古|内蒙古、河北|河北、辽宁|河北、辽宁等|内蒙古、黑龙江|黑龙江、吉林|吉林、辽宁|山东、河南|河北、山东|河南、山东|辽宁、吉林|四川、重庆|广东、广西|北京、天津|上海、江苏|湖南、湖北|陕西、山西|甘肃、宁夏|新疆、青海|云南、贵州|福建、江西|广西、海南|安徽、江苏|浙江、福建|江西、湖南)[^。；，]*?)?)\s*([^，。；]{1,18}?)\s*平均价格\s*([0-9]+(?:\.[0-9]+)?)\s*元\/(公斤|只|吨|千克)(?:，|。|；)(?:比前一周|与前一周|同比|环比)?([^。；]*)/g;
    let match;
    while ((match = avgRe.exec(sentence))) {
      const scope = normalizeSpacing(match[1] || "全国");
      const item = normalizeSpacing(match[2]).replace(/^全国/, "");
      const price = normalizeNumber(match[3]);
      const unit = `元/${match[4]}`;
      const tail = match[5] || "";
      const mom = tail.match(/(?:比前一周|环比)(上涨|下跌)\s*([0-9]+(?:\.[0-9]+)?)%|与前一周持平/);
      const yoy = tail.match(/同比(上涨|下跌|持平)\s*([0-9]+(?:\.[0-9]+)?)?%?/);
      addMetric(rows, article, {
        category,
        scope,
        item,
        metric: "平均价格",
        value: price,
        unit,
        momDirection: mom?.[0]?.includes("持平") ? "持平" : normalizeDirection(mom?.[1] || ""),
        momPct: mom?.[0]?.includes("持平") ? 0 : signedPct(mom?.[1] || "", normalizeNumber(mom?.[2])),
        yoyDirection: normalizeDirection(yoy?.[1] || ""),
        yoyPct: yoy ? signedPct(yoy[1] || "持平", normalizeNumber(yoy[2] || "0")) : null,
        context: sentence,
      });
    }

    const mainRe = /(?:全国|主产省份|主产区东北三省|主销区广东省)\s*([^，。；]{1,18}?)价格\s*([0-9]+(?:\.[0-9]+)?)\s*元\/(公斤|只|吨|千克)(?:，|。|；)([^。；]*)/g;
    while ((match = mainRe.exec(sentence))) {
      const item = normalizeSpacing(match[1]).replace(/^平均/, "");
      const price = normalizeNumber(match[2]);
      const unit = `元/${match[3]}`;
      const tail = match[4] || "";
      const mom = tail.match(/(?:比前一周|环比)(上涨|下跌)\s*([0-9]+(?:\.[0-9]+)?)%|与前一周持平/);
      const yoy = tail.match(/同比(上涨|下跌|持平)\s*([0-9]+(?:\.[0-9]+)?)?%?/);
      addMetric(rows, article, {
        category,
        scope: sentence.startsWith("全国") ? "全国" : "主产省份/区域",
        item,
        metric: "价格",
        value: price,
        unit,
        momDirection: mom?.[0]?.includes("持平") ? "持平" : normalizeDirection(mom?.[1] || ""),
        momPct: mom?.[0]?.includes("持平") ? 0 : signedPct(mom?.[1] || "", normalizeNumber(mom?.[2])),
        yoyDirection: normalizeDirection(yoy?.[1] || ""),
        yoyPct: yoy ? signedPct(yoy[1] || "持平", normalizeNumber(yoy[2] || "0")) : null,
        context: sentence,
      });
    }
  }

  const seen = new Set();
  return rows.filter((row) => {
    const key = [row.publishDate, row.item, row.scope, row.value, row.unit, row.context].join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function extractSlaughterMetrics(article) {
  const rows = [];
  if (article.type !== "生猪定点屠宰价格") return rows;

  for (const sentence of sentenceSplit(article.text)) {
    const re = /(?:全国规模以上生猪定点屠宰企业|全国生猪定点屠宰企业|生猪定点屠宰企业)?\s*([^，。；]*?(?:收购|出厂)[^，。；]*?)\s*(?:平均价格|价格)?\s*为?\s*([0-9]+(?:\.[0-9]+)?)\s*元\/公斤(?:，|。|；)?([^。；]*)/g;
    let match;
    while ((match = re.exec(sentence))) {
      const itemRaw = normalizeSpacing(match[1]);
      const item = itemRaw.includes("白条") ? "白条肉出厂价格" : itemRaw.includes("生猪") ? "生猪收购价格" : itemRaw;
      const tail = match[3] || "";
      const mom = tail.match(/(?:环比|比前一周|比上周)(上涨|下跌)\s*([0-9]+(?:\.[0-9]+)?)%|(?:环比|比前一周|比上周)持平/);
      const yoy = tail.match(/同比(上涨|下跌|持平)\s*([0-9]+(?:\.[0-9]+)?)?%?/);
      addMetric(rows, article, {
        scope: "全国",
        item,
        metric: "平均价格",
        value: normalizeNumber(match[2]),
        unit: "元/公斤",
        momDirection: mom?.[0]?.includes("持平") ? "持平" : normalizeDirection(mom?.[1] || ""),
        momPct: mom?.[0]?.includes("持平") ? 0 : signedPct(mom?.[1] || "", normalizeNumber(mom?.[2])),
        yoyDirection: normalizeDirection(yoy?.[1] || ""),
        yoyPct: yoy ? signedPct(yoy[1] || "持平", normalizeNumber(yoy[2] || "0")) : null,
        context: sentence,
      });
    }
  }

  const seen = new Set();
  return rows.filter((row) => {
    const key = [row.publishDate, row.item, row.value, row.context].join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function main() {
  await fs.mkdir(RAW_DIR, { recursive: true });

  const firstHtml = await fetchText(BASE);
  const countPage = extractCountPage(firstHtml);
  const requestedPages = PAGES_TO_FETCH === "all"
    ? countPage
    : Math.max(1, Math.min(countPage, Number.parseInt(PAGES_TO_FETCH, 10) || 1));
  const listPages = [];
  const articlesByUrl = new Map();

  for (let i = 0; i < requestedPages; i += 1) {
    const url = pageUrl(i);
    const html = i === 0 ? firstHtml : await fetchText(url);
    await fs.writeFile(path.join(RAW_DIR, `list_${String(i + 1).padStart(2, "0")}.html`), html, "utf8");
    const items = extractListItems(html, url);
    listPages.push({ page: i + 1, url, itemCount: items.length });
    for (const item of items) {
      if (!articlesByUrl.has(item.url)) articlesByUrl.set(item.url, { ...item, listPage: i + 1 });
    }
    await sleep(120);
  }

  const articles = [];
  const marketMetrics = [];
  const slaughterMetrics = [];
  let index = 0;
  for (const [url, item] of articlesByUrl.entries()) {
    index += 1;
    const html = await fetchText(url);
    const rawName = `article_${String(index).padStart(4, "0")}_${url.match(/t\d+_\d+/)?.[0] || "page"}.html`;
    await fs.writeFile(path.join(RAW_DIR, rawName), html, "utf8");
    const article = extractArticle(html, url, item);
    articles.push({ ...article, ordinal: index });
    marketMetrics.push(...extractMarketMetrics(article));
    slaughterMetrics.push(...extractSlaughterMetrics(article));
    await sleep(80);
  }

  const data = {
    generatedAt: new Date().toISOString(),
    source: BASE,
    countPage,
    listPages,
    articleCount: articles.length,
    articles,
    marketMetrics,
    slaughterMetrics,
    extractionNotes: [
      "文章目录和原文内容为本次抓取范围内的官网原文。",
      `本次抓取列表页数：${requestedPages}/${countPage}。默认只抓最新列表页，历史价格由模板工作簿保留并合并。`,
      "结构化指标通过规则从正文句子中抽取，保留 context 字段用于核对。",
      "无法稳定归入固定指标的文字保留在原文内容表中。",
    ],
  };

  await fs.writeFile(DATA_JSON, JSON.stringify(data, null, 2), "utf8");
  console.log(JSON.stringify({
    countPage,
    requestedPages,
    articleCount: articles.length,
    marketMetricRows: marketMetrics.length,
    slaughterMetricRows: slaughterMetrics.length,
    output: DATA_JSON,
  }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
