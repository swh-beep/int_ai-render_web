import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable


def load_analyzed_items(
    *,
    furniture_data: list | None,
    moodboard_url: str | None,
    local_path: str,
    materialize_input: Callable[[str | None, str], str | None],
    detect_furniture_boxes: Callable[[str], list],
    canonical_category: Callable[[str | None], str],
    build_item_target_key: Callable[..., str],
    max_concurrency_analysis: int,
    analyze_cropped_item: Callable[[str, dict], dict],
    attach_volume_ranks: Callable[[list], list],
) -> list:
    analyzed_items = []
    if furniture_data and len(furniture_data) > 0:
        print(">> [Smart Cache] Using pre-analyzed furniture data!", flush=True)
        analyzed_items = furniture_data
    else:
        print(">> [Smart Cache] No cached data found. Starting Analysis...", flush=True)

        target_analysis_path = None
        if moodboard_url:
            if moodboard_url.startswith("/assets/"):
                rel_path = moodboard_url.lstrip("/")
                target_analysis_path = os.path.join(*rel_path.split("/"))
            else:
                target_analysis_path = materialize_input(moodboard_url, "mb")
        else:
            print(">> [Info] No Moodboard provided. Analyzing the Main Image itself.", flush=True)
            target_analysis_path = local_path

        if target_analysis_path and os.path.exists(target_analysis_path):
            try:
                detected_items = detect_furniture_boxes(target_analysis_path)
                print(f">> [Deep Analysis] Found {len(detected_items)} items in {target_analysis_path}...", flush=True)

                enriched_items = []
                for index, item in enumerate(detected_items or [], start=1):
                    if not isinstance(item, dict):
                        continue
                    label_val = item.get("label") or f"Item{index}"
                    row = dict(item)
                    row["source_index"] = index
                    row["category_canonical"] = canonical_category(label_val)
                    row["target_key"] = build_item_target_key("detail", index, label=label_val)
                    enriched_items.append(row)

                analysis_workers = min(max_concurrency_analysis, max(1, len(enriched_items)))
                with ThreadPoolExecutor(max_workers=analysis_workers) as executor:
                    futures = [executor.submit(analyze_cropped_item, target_analysis_path, item) for item in enriched_items]
                    analyzed_items = [future.result() for future in futures]
            except Exception as exc:
                print(f"!! [Deep Analysis Failed] {exc}", flush=True)

    if not analyzed_items:
        analyzed_items = [{"label": "Main Furniture", "description": "High quality furniture matching the room style."}]

    try:
        analyzed_items = attach_volume_ranks(analyzed_items)
    except Exception:
        pass

    return analyzed_items
