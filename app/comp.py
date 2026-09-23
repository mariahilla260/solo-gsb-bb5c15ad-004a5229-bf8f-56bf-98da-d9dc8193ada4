"""多轨剪辑方案（comp plan）：按小节从多条已对齐录音中选段拼接。

输入 ≥2 条共享**同一参考节拍网格**、且均已完成对齐的任务（每个任务对应
一条录音 / take）。把参考拍按 ``beats_per_measure`` 划分成小节（末尾不足
整小节的尾小节同样作为一节处理）：

* 一条录音仅在**完整匹配该小节全部参考拍**（小节内无漏拍）时才是该小节的
  候选录音；
* 选用代价 = 该小节各匹配拍 ``|perf_time − ref_time|`` 的平均值，即该小节
  匹配拍的**平均绝对偏差**；
* 相邻小节若选自不同任务（换轨），加收一次非负的 ``switch_cost``。

用动态规划最小化「各小节选用代价 + 相邻小节换轨代价」之和；总代价相同
时，优先请求顺序靠前的任务（选出的任务序号序列按请求顺序字典序最小）。
最后把**连续同源**（同一任务）的相邻小节合并为输出片段，给出任务 ID、
参考拍索引范围与来源首末匹配音时间。该计算按需进行、只读，不落库。
"""

from __future__ import annotations

from typing import Any, Sequence

_DECIMALS = 6

# 一次剪辑方案请求至少需要多少个任务
MIN_TAKES = 2


class CompPlanError(ValueError):
    """剪辑方案输入非法。"""


def _round6(value: float) -> float:
    """四舍五入到 6 位小数并消除 -0.0（对齐结果本身也是 6 位小数口径）。"""
    rounded = round(value, _DECIMALS)
    return 0.0 if rounded == 0.0 else rounded


def build_comp_plan(
    jobs: Sequence[dict[str, Any]],
    beats_per_measure: int,
    switch_cost: float,
) -> dict[str, Any]:
    """对参考网格完全相同的已完成任务序列计算按小节选轨的剪辑方案。

    ``jobs`` 为按请求顺序排列的 crud 任务字典（至少 2 个，``status='done'``
    且 ``reference_beats`` 完全一致）；``beats_per_measure`` 为每小节拍数
    （正整数），``switch_cost`` 为相邻小节换轨的非负代价。返回逐小节选轨
    明细与合并连续同源小节后的片段。
    """
    if beats_per_measure < 1:
        raise CompPlanError("每小节拍数 beats_per_measure 必须为正整数")
    if switch_cost < 0:
        raise CompPlanError("换轨代价 switch_cost 不能为负")
    if len(jobs) < MIN_TAKES:
        raise CompPlanError(f"剪辑方案至少需要 {MIN_TAKES} 个已完成任务")

    reference_beats = jobs[0]["reference_beats"]
    n_ref = len(reference_beats)
    n_jobs = len(jobs)

    # ---- 1. 按参考拍划小节：[lo, hi) 为 0 基索引区间（含末尾不足整小节） ----
    measure_ranges = [
        (start, min(start + beats_per_measure, n_ref))
        for start in range(0, n_ref, beats_per_measure)
    ]
    n_measures = len(measure_ranges)

    # 每条轨建 参考拍索引 -> 匹配拍 映射
    matched_by_track: list[dict[int, dict[str, Any]]] = []
    for job in jobs:
        matched = (job.get("result") or {}).get("matched_beats") or []
        matched_by_track.append({beat["ref_index"]: beat for beat in matched})

    # ---- 2. 逐小节收集候选录音：仅完整匹配该小节全部拍的轨可选 ----
    # candidates[m] 按请求顺序（轨序号升序）保存该小节候选
    candidates: list[list[dict[str, Any]]] = []
    for measure_index, (lo, hi) in enumerate(measure_ranges):
        options: list[dict[str, Any]] = []
        for track_index, by_ref in enumerate(matched_by_track):
            beats = [by_ref.get(ri) for ri in range(lo, hi)]
            if any(beat is None for beat in beats):
                continue  # 小节内存在漏拍 -> 该轨本小节不可选
            mean_abs_dev = sum(abs(b["deviation"]) for b in beats) / len(beats)
            options.append({
                "track": track_index,
                "job_id": jobs[track_index]["id"],
                "name": jobs[track_index].get("name"),
                "mean_absolute_deviation": _round6(mean_abs_dev),
                "beat_count": len(beats),
                "first_perf_time": _round6(float(beats[0]["perf_time"])),
                "last_perf_time": _round6(float(beats[-1]["perf_time"])),
            })
        if not options:
            raise CompPlanError(
                f"第 {measure_index + 1} 小节没有任何录音完整匹配该小节全部"
                f" {hi - lo} 拍，无法生成剪辑方案"
            )
        candidates.append(options)

    # ---- 3. 动态规划（自末小节反向递推） ----
    # best[m][t] = 第 m 小节选轨 t（t 为该小节候选轨序号）时，第 m..末小节
    # 的最小总代价（含 m 小节选用代价）。反向递推后再从首小节按轨序号升序
    # 贪心回溯：总代价相同时天然得到请求顺序字典序最小的选轨序列。
    best: list[dict[int, float]] = [dict() for _ in range(n_measures)]
    best[n_measures - 1] = {
        opt["track"]: opt["mean_absolute_deviation"] for opt in candidates[-1]
    }
    for measure_index in range(n_measures - 2, -1, -1):
        row: dict[int, float] = {}
        for opt in candidates[measure_index]:
            track = opt["track"]
            # 与回溯同口径：每个转移值先做 6 位小数舍入再取最小，
            # 避免浮点尾数 / 嵌套舍入导致正向选轨与 DP 最优值不一致
            best_next = min(
                _round6(
                    best[measure_index + 1][next_opt["track"]]
                    + (0.0 if next_opt["track"] == track else switch_cost)
                )
                for next_opt in candidates[measure_index + 1]
            )
            row[track] = _round6(opt["mean_absolute_deviation"] + best_next)
        best[measure_index] = row

    # ---- 4. 自首小节回溯：候选按轨序号升序，严格小于才更新 -> 同代价取最早 ----
    chosen_tracks: list[int] = []
    previous_track: int | None = None
    for measure_index in range(n_measures):
        chosen_track = candidates[measure_index][0]["track"]
        chosen_value = float("inf")
        for opt in candidates[measure_index]:
            track = opt["track"]
            switch = (
                0.0
                if previous_track is None or track == previous_track
                else switch_cost
            )
            value = _round6(switch + best[measure_index][track])
            if value < chosen_value:
                chosen_value = value
                chosen_track = track
        chosen_tracks.append(chosen_track)
        previous_track = chosen_track
    total_cost = best[0][chosen_tracks[0]]
    switch_count = sum(
        1
        for i in range(1, n_measures)
        if chosen_tracks[i] != chosen_tracks[i - 1]
    )

    option_by_track = [
        {opt["track"]: opt for opt in options} for options in candidates
    ]

    # ---- 5. 逐小节选轨明细 ----
    measures: list[dict[str, Any]] = []
    for measure_index, (lo, hi) in enumerate(measure_ranges):
        track = chosen_tracks[measure_index]
        opt = option_by_track[measure_index][track]
        measures.append({
            "measure_index": measure_index,
            "measure_number": measure_index + 1,
            "ref_start_index": lo,
            "ref_end_index": hi - 1,
            "beat_number_start": lo + 1,
            "beat_number_end": hi,
            "beat_count": hi - lo,
            "job_id": opt["job_id"],
            "mean_absolute_deviation": opt["mean_absolute_deviation"],
            "source_first_match_time": opt["first_perf_time"],
            "source_last_match_time": opt["last_perf_time"],
            "switched_from_previous": (
                measure_index > 0
                and chosen_tracks[measure_index] != chosen_tracks[measure_index - 1]
            ),
            "candidates": [
                {
                    "job_id": candidate["job_id"],
                    "mean_absolute_deviation": candidate["mean_absolute_deviation"],
                }
                for candidate in candidates[measure_index]
            ],
        })

    # ---- 6. 合并连续同源小节为片段 ----
    segments: list[dict[str, Any]] = []
    for measure_index, (lo, hi) in enumerate(measure_ranges):
        track = chosen_tracks[measure_index]
        opt = option_by_track[measure_index][track]
        if segments and segments[-1]["_track"] == track:
            segment = segments[-1]
            segment["measure_end_index"] = measure_index
            segment["ref_end_index"] = hi - 1
            segment["beat_number_end"] = hi
            segment["measure_count"] += 1
            segment["beat_count"] += hi - lo
            segment["source_end_time"] = opt["last_perf_time"]
        else:
            segments.append({
                "_track": track,
                "job_id": opt["job_id"],
                "name": opt["name"],
                "measure_start_index": measure_index,
                "measure_end_index": measure_index,
                "ref_start_index": lo,
                "ref_end_index": hi - 1,
                "beat_number_start": lo + 1,
                "beat_number_end": hi,
                "measure_count": 1,
                "beat_count": hi - lo,
                "source_start_time": opt["first_perf_time"],
                "source_end_time": opt["last_perf_time"],
            })

    segment_out: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(segments):
        segment_out.append({
            "segment_index": segment_index,
            "job_id": segment["job_id"],
            "name": segment["name"],
            "measure_start_index": segment["measure_start_index"],
            "measure_end_index": segment["measure_end_index"],
            "measure_number_start": segment["measure_start_index"] + 1,
            "measure_number_end": segment["measure_end_index"] + 1,
            "measure_count": segment["measure_count"],
            "ref_start_index": segment["ref_start_index"],
            "ref_end_index": segment["ref_end_index"],
            "beat_number_start": segment["beat_number_start"],
            "beat_number_end": segment["beat_number_end"],
            "beat_count": segment["beat_count"],
            "source_start_time": segment["source_start_time"],
            "source_end_time": segment["source_end_time"],
        })

    return {
        "parameters": {
            "beats_per_measure": beats_per_measure,
            "switch_cost": switch_cost,
            "min_takes": MIN_TAKES,
        },
        "job_ids": [job["id"] for job in jobs],
        "reference_beats": [_round6(float(t)) for t in reference_beats],
        "reference_beat_count": n_ref,
        "take_count": n_jobs,
        "measure_count": n_measures,
        "segment_count": len(segment_out),
        "switch_count": switch_count,
        "total_cost": _round6(total_cost),
        "measures": measures,
        "segments": segment_out,
    }
