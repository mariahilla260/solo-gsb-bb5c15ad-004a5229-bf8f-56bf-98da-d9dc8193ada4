"""多轨剪辑方案（comp plan）纯算法单元测试。"""

import unittest

from app.comp import CompPlanError, build_comp_plan

REF6 = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
REF4 = [0.0, 0.5, 1.0, 1.5]


def _take(job_id, deviations, ref=REF6, name=None):
    """构造与 crud 行同构的任务字典。

    ``deviations`` 为 {参考拍索引: 偏差秒}；未列出的参考拍视为漏拍
    （该轨对该拍没有匹配）。
    """
    matched, missed = [], []
    perf_index = 0
    for ri, ref_time in enumerate(ref):
        if ri in deviations:
            dev = round(deviations[ri], 6)
            status = (
                "on_time" if abs(dev) <= 0.03
                else ("early" if dev < 0 else "late")
            )
            matched.append({
                "ref_index": ri,
                "perf_index": perf_index,
                "ref_time": ref_time,
                "perf_time": round(ref_time + dev, 6),
                "deviation": dev,
                "status": status,
            })
            perf_index += 1
        else:
            missed.append({"ref_index": ri, "ref_time": ref_time})
    return {
        "id": job_id,
        "name": name or f"take-{job_id}",
        "reference_beats": list(ref),
        "status": "done",
        "result": {
            "matched_beats": matched,
            "missed_beats": missed,
        },
    }


def _job_ids(out):
    return [m["job_id"] for m in out["measures"]]


class TestCompPlan(unittest.TestCase):
    def test_picks_better_take_whole_piece(self):
        # A 全程偏差 0.01，B 全程偏差 0.05；换轨代价再高也选 A
        jobs = [
            _take(1, {ri: 0.01 for ri in range(6)}),
            _take(2, {ri: 0.05 for ri in range(6)}),
        ]
        out = build_comp_plan(jobs, beats_per_measure=3, switch_cost=1.0)
        self.assertEqual(_job_ids(out), [1, 1])
        self.assertEqual(out["measure_count"], 2)
        self.assertEqual(out["segment_count"], 1)
        self.assertEqual(out["switch_count"], 0)
        self.assertAlmostEqual(out["total_cost"], 0.02, places=6)
        seg = out["segments"][0]
        self.assertEqual(seg["job_id"], 1)
        self.assertEqual(seg["measure_count"], 2)
        self.assertEqual(seg["beat_count"], 6)
        self.assertEqual((seg["ref_start_index"], seg["ref_end_index"]), (0, 5))
        self.assertEqual((seg["beat_number_start"], seg["beat_number_end"]), (1, 6))
        self.assertAlmostEqual(seg["source_start_time"], 0.01, places=6)
        self.assertAlmostEqual(seg["source_end_time"], 2.51, places=6)
        # 每小节两个候选，按请求顺序
        for measure in out["measures"]:
            self.assertEqual([c["job_id"] for c in measure["candidates"]], [1, 2])

    def test_switch_to_better_take_when_cheaper(self):
        # A 首小节更准（0.01）尾小节差（0.10），B 相反（0.05 / 0.01）；
        # 换轨代价 0.02 -> A,B（0.01+0.01+0.02=0.05）最优
        jobs = [
            _take(1, {0: 0.01, 1: 0.01, 2: 0.01, 3: 0.10, 4: 0.10, 5: 0.10}),
            _take(2, {0: 0.05, 1: 0.05, 2: 0.05, 3: 0.01, 4: 0.01, 5: 0.01}),
        ]
        out = build_comp_plan(jobs, beats_per_measure=3, switch_cost=0.02)
        self.assertEqual(_job_ids(out), [1, 2])
        self.assertEqual(out["switch_count"], 1)
        self.assertAlmostEqual(out["total_cost"], 0.04, places=6)
        self.assertEqual(out["segment_count"], 2)
        self.assertFalse(out["measures"][0]["switched_from_previous"])
        self.assertTrue(out["measures"][1]["switched_from_previous"])
        first, second = out["segments"]
        self.assertEqual((first["job_id"], first["measure_number_start"],
                          first["measure_number_end"]), (1, 1, 1))
        self.assertEqual((second["job_id"], second["measure_number_start"],
                          second["measure_number_end"]), (2, 2, 2))

    def test_dp_beats_per_measure_greedy(self):
        # 逐小节贪心会选 A,B（0.02 + 0.01+0.03 换轨 = 0.06），
        # 但全程用 B 只要 0.03+0.01=0.04 -> DP 选 B,B
        jobs = [
            _take(1, {0: 0.02, 1: 0.02, 2: 0.02, 3: 0.09, 4: 0.09, 5: 0.09}),
            _take(2, {0: 0.03, 1: 0.03, 2: 0.03, 3: 0.01, 4: 0.01, 5: 0.01}),
        ]
        out = build_comp_plan(jobs, beats_per_measure=3, switch_cost=0.03)
        self.assertEqual(_job_ids(out), [2, 2])
        self.assertAlmostEqual(out["total_cost"], 0.04, places=6)
        self.assertEqual(out["switch_count"], 0)

    def test_global_tie_prefers_earlier_track_sequence(self):
        # A: 0.01/0.06，B: 0.06/0.01，换轨 0.05：
        # A,A = A,B = B,B = 0.07 三方并列 -> 请求顺序字典序最小的 [A,A]
        jobs = [
            _take(1, {0: 0.01, 1: 0.01, 2: 0.01, 3: 0.06, 4: 0.06, 5: 0.06}),
            _take(2, {0: 0.06, 1: 0.06, 2: 0.06, 3: 0.01, 4: 0.01, 5: 0.01}),
        ]
        out = build_comp_plan(jobs, beats_per_measure=3, switch_cost=0.05)
        self.assertEqual(_job_ids(out), [1, 1])
        self.assertEqual(out["switch_count"], 0)
        self.assertAlmostEqual(out["total_cost"], 0.07, places=6)

    def test_tie_between_stay_and_switch_keeps_earlier(self):
        # 同上数据但换轨代价 0.05 时第 2 小节“留 A(0.06)”与“换 B(0.01+0.05)”
        # 同代价；回溯按轨序号升序严格取小 -> 留在更早请求的 A
        jobs = [
            _take(1, {0: 0.01, 1: 0.01, 2: 0.01, 3: 0.06, 4: 0.06, 5: 0.06}),
            _take(2, {0: 0.06, 1: 0.06, 2: 0.06, 3: 0.01, 4: 0.01, 5: 0.01}),
        ]
        out = build_comp_plan(jobs, beats_per_measure=3, switch_cost=0.05)
        self.assertEqual(out["measures"][1]["job_id"], 1)

    def test_zero_switch_cost_switches_freely(self):
        # 换轨免费：每小节取局部最优 -> A,B
        jobs = [
            _take(1, {0: 0.01, 1: 0.01, 2: 0.01, 3: 0.06, 4: 0.06, 5: 0.06}),
            _take(2, {0: 0.06, 1: 0.06, 2: 0.06, 3: 0.01, 4: 0.01, 5: 0.01}),
        ]
        out = build_comp_plan(jobs, beats_per_measure=3, switch_cost=0.0)
        self.assertEqual(_job_ids(out), [1, 2])
        self.assertEqual(out["switch_count"], 1)
        self.assertAlmostEqual(out["total_cost"], 0.02, places=6)

    def test_measure_without_any_complete_take_raises(self):
        # 两轨在第 1 小节都漏了第 2 拍（索引 1）-> 第 1 小节无候选
        jobs = [
            _take(1, {ri: 0.01 for ri in range(6) if ri != 1}),
            _take(2, {ri: 0.02 for ri in range(6) if ri != 1}),
        ]
        with self.assertRaises(CompPlanError) as ctx:
            build_comp_plan(jobs, beats_per_measure=3, switch_cost=0.0)
        self.assertIn("第 1 小节", str(ctx.exception))

    def test_partial_tail_measure_is_used(self):
        # 4 拍、每小节 3 拍 -> 第 2 小节只有 1 拍；B 漏掉末拍，尾小节仅 A 可选
        jobs = [
            _take(1, {ri: 0.01 for ri in range(4)}, ref=REF4),
            _take(2, {ri: 0.0 for ri in range(3)}, ref=REF4),
        ]
        out = build_comp_plan(jobs, beats_per_measure=3, switch_cost=1.0)
        self.assertEqual(out["measure_count"], 2)
        tail = out["measures"][1]
        self.assertEqual(tail["beat_count"], 1)
        self.assertEqual((tail["ref_start_index"], tail["ref_end_index"]), (3, 3))
        self.assertEqual((tail["beat_number_start"], tail["beat_number_end"]), (4, 4))
        self.assertEqual([c["job_id"] for c in tail["candidates"]], [1])
        self.assertEqual(tail["job_id"], 1)

    def test_incomplete_take_excluded_from_measure_candidates(self):
        # 单小节 4 拍：B 漏第 3 拍 -> 候选只有 A
        jobs = [
            _take(1, {ri: 0.02 for ri in range(4)}, ref=REF4),
            _take(2, {ri: 0.0 for ri in range(4) if ri != 2}, ref=REF4),
        ]
        out = build_comp_plan(jobs, beats_per_measure=4, switch_cost=0.0)
        measure = out["measures"][0]
        self.assertEqual([c["job_id"] for c in measure["candidates"]], [1])
        self.assertEqual(measure["job_id"], 1)
        self.assertAlmostEqual(
            measure["mean_absolute_deviation"], 0.02, places=6
        )

    def test_mean_absolute_deviation_cost(self):
        # 小节内偏差 [0.02, -0.04, 0.00] -> 平均绝对偏差 0.02
        jobs = [
            _take(1, {0: 0.02, 1: -0.04, 2: 0.00, 3: 0.02, 4: -0.04, 5: 0.00}),
            _take(2, {ri: 0.09 for ri in range(6)}),
        ]
        out = build_comp_plan(jobs, beats_per_measure=3, switch_cost=0.0)
        self.assertAlmostEqual(
            out["measures"][0]["mean_absolute_deviation"], 0.02, places=6
        )
        self.assertAlmostEqual(out["total_cost"], 0.04, places=6)

    def test_single_measure_tie_prefers_first_requested(self):
        # 单小节三轨代价完全相同 -> 请求顺序第一的轨
        jobs = [
            _take(1, {ri: 0.05 for ri in range(2)}, ref=[0.0, 0.5]),
            _take(2, {ri: 0.05 for ri in range(2)}, ref=[0.0, 0.5]),
            _take(3, {ri: 0.05 for ri in range(2)}, ref=[0.0, 0.5]),
        ]
        out = build_comp_plan(jobs, beats_per_measure=2, switch_cost=0.1)
        self.assertEqual(_job_ids(out), [1])
        self.assertEqual(out["segment_count"], 1)
        self.assertEqual(out["take_count"], 3)
        self.assertEqual(out["job_ids"], [1, 2, 3])

    def test_validation_errors(self):
        good = [
            _take(1, {ri: 0.01 for ri in range(6)}),
            _take(2, {ri: 0.02 for ri in range(6)}),
        ]
        with self.assertRaises(CompPlanError):
            build_comp_plan(good[:1], beats_per_measure=3, switch_cost=0.0)
        with self.assertRaises(CompPlanError):
            build_comp_plan(good, beats_per_measure=0, switch_cost=0.0)
        with self.assertRaises(CompPlanError):
            build_comp_plan(good, beats_per_measure=3, switch_cost=-0.01)


if __name__ == "__main__":
    unittest.main()
