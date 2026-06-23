#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def article_sort_key(article):
    return (
        article.get("publishDate") or "",
        str(article.get("ordinal") or "").zfill(8),
        article.get("url") or "",
    )


def merge_articles(baseline_articles, latest_articles):
    by_url = {}
    for article in baseline_articles:
        url = article.get("url")
        if url:
            by_url[url] = article
    for article in latest_articles:
        url = article.get("url")
        if url:
            by_url[url] = article
    articles = sorted(by_url.values(), key=article_sort_key, reverse=True)
    for idx, article in enumerate(articles, start=1):
        article["ordinal"] = idx
    return articles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--latest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    latest = json.loads(Path(args.latest).read_text(encoding="utf-8"))
    articles = merge_articles(baseline.get("articles", []), latest.get("articles", []))

    merged = {
        **baseline,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": latest.get("source") or baseline.get("source"),
        "countPage": latest.get("countPage") or baseline.get("countPage"),
        "requestedPages": latest.get("requestedPages"),
        "listPages": latest.get("listPages", []),
        "articleCount": len(articles),
        "articles": articles,
        "marketMetrics": [],
        "slaughterMetrics": [],
        "extractionNotes": [
            "baseline_moa_jcyj_data.json 为历史基线，本次最新抓取结果按 URL 合并。",
            "Excel 生成时会从合并后的 articles 重新抽取结构化价格。",
            f"本次最新抓取文章数：{latest.get('articleCount', len(latest.get('articles', [])))}；合并后文章数：{len(articles)}。",
        ],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "articleCount": len(articles)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
