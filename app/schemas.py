"""请求 / 响应 Pydantic 模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

BeatStatus = Literal["early", "on_time", "late"]


class AlignmentParamsPayload(BaseModel):
    band_width: int = Field(
        default=3, ge=1, le=10000,
        description="Sakoe-Chiba 带宽：允许 |参考索引-演奏索引| 偏离的最大拍数",
    )
    skip_penalty: float | None = Field(
        default=None, ge=0,
        description="漏一拍的代价（秒²）；默认 (0.5×中位拍间隔)²",
    )
    insert_penalty: float | None = Field(
        default=None, ge=0,
        description="插入一拍的代价（秒²）；默认与漏拍代价相同",
    )
    on_time_threshold: float = Field(
        default=0.03, gt=0,
        description="|偏差|≤该秒数判定为准时",
    )


class AnchorPair(BaseModel):
    ref_index: int = Field(
        ..., description="参考拍索引（0 基），锚点表示该参考拍必须与该演奏音匹配"
    )
    perf_index: int = Field(..., description="演奏音索引（0 基）")


class JobCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200, description="任务名称")
    reference_beats: list[float] = Field(
        ..., min_length=1, description="参考节拍网格时间（秒），需严格递增"
    )
    performance_onsets: list[float] = Field(
        ..., min_length=1, description="演奏音符起始时间（秒），需严格递增"
    )
    anchors: list[AnchorPair] | None = Field(
        default=None,
        description=(
            "锚点硬约束（按索引一一对应）：对齐路径必须经过这些 参考拍↔演奏音 匹配点；"
            "参考拍索引与演奏音索引均需严格递增、不越界且在带宽内可达"
        ),
    )
    params: AlignmentParamsPayload = Field(default_factory=AlignmentParamsPayload)

    @field_validator("reference_beats", "performance_onsets")
    @classmethod
    def _strictly_increasing(cls, v: list[float]) -> list[float]:
        # 允许负值（演奏音可能略早于第 0 拍，即前摇），但必须严格递增
        if any(v[i] <= v[i - 1] for i in range(1, len(v))):
            raise ValueError("时间序列必须严格递增")
        return v


class BeatPair(BaseModel):
    ref_index: int
    perf_index: int
    ref_time: float
    perf_time: float
    deviation: float
    status: BeatStatus


class MissedBeat(BaseModel):
    ref_index: int
    ref_time: float


class InsertedBeat(BaseModel):
    perf_index: int
    perf_time: float


class DeviationSegment(BaseModel):
    segment_index: int
    ref_start_index: int
    ref_end_index: int
    ref_start_time: float
    ref_end_time: float
    beat_count: int
    min_deviation: float
    max_deviation: float
    mean_deviation: float
    beats: list[BeatPair]


class SignedInterval(BaseModel):
    segment_index: int
    status: BeatStatus
    ref_start_index: int
    ref_end_index: int
    ref_start_time: float
    ref_end_time: float
    beat_count: int
    min_deviation: float
    max_deviation: float
    mean_deviation: float


class AdoptedAnchor(BaseModel):
    ref_index: int
    perf_index: int
    ref_time: float
    perf_time: float
    deviation: float
    status: BeatStatus


class AlignmentResult(BaseModel):
    parameters: dict[str, Any]
    anchors: list[AdoptedAnchor] = Field(
        default_factory=list,
        description="本次对齐实际采用的锚点（与请求中给出的硬约束一致）",
    )
    matched_beats: list[BeatPair]
    missed_beats: list[MissedBeat]
    inserted_beats: list[InsertedBeat]
    deviation_segments: list[DeviationSegment]
    signed_intervals: list[SignedInterval]
    statistics: dict[str, Any]


class TempoEvent(BaseModel):
    ref_start_index: int = Field(..., description="事件起拍：第一个匹配参考拍的 0 基索引")
    ref_end_index: int = Field(
        ..., description="事件结束拍（含）：区间内最后一个匹配参考拍的 0 基索引"
    )
    start_time: float = Field(..., description="事件起拍对应的演奏时间（秒）")
    ref_time: float = Field(..., description="事件起拍对应的参考时间（秒）")
    bpm: float = Field(..., description="该稳定区间的估计 BPM（区间内稳健 BPM 的中位数）")
    beat_count: int = Field(
        ...,
        description=(
            "覆盖拍数（ref_end_index − ref_start_index），被忽略间隔跨过的漏拍也计入；"
            "相邻事件共享边界拍"
        ),
    )
    gap_count: int = Field(
        ..., description="并入本事件的相邻匹配间隔数（被忽略的异常间隔不计）"
    )


class TempoMapOut(BaseModel):
    job_id: int
    parameters: dict[str, Any]
    matched_beat_count: int = Field(..., description="本次估计使用的匹配拍数")
    events: list[TempoEvent] = Field(
        default_factory=list,
        description="按参考拍排序的 tempo events（稳定 BPM 区间，自该事件起拍生效）",
    )
    ignored_ref_indices: list[int] = Field(
        default_factory=list,
        description="被忽略的异常间隔起始参考拍索引（漏拍 / 插入拍残留或超 BPM 范围）",
    )


ReviewIssueType = Literal["missed", "inserted", "deviation"]


class ReviewIssue(BaseModel):
    type: ReviewIssueType = Field(
        ..., description="问题类型：missed=漏拍，inserted=插入音，deviation=超阈值偏差"
    )
    ref_index: int | None = Field(
        ..., description="关联的参考拍 0 基索引（插入音无对应参考拍，为 null）"
    )
    beat_number: int | None = Field(
        ..., description="关联的 1 基拍号（插入音为 null）"
    )
    perf_index: int | None = Field(..., description="关联的演奏音 0 基索引（漏拍为 null）")
    ref_time: float | None = Field(..., description="参考时间（秒）；插入音为 null")
    perf_time: float | None = Field(..., description="演奏时间（秒）；漏拍为 null")
    deviation: float | None = Field(
        ..., description="偏差（秒，perf−ref）；仅超阈值偏差问题给出"
    )
    status: BeatStatus | None = Field(
        ..., description="early/late；仅超阈值偏差问题给出"
    )
    description: str = Field(..., description="单问题的中文描述")


class ReviewSegment(BaseModel):
    segment_index: int
    ref_start_index: int = Field(..., description="片段起始参考拍 0 基索引")
    ref_end_index: int = Field(..., description="片段结束参考拍 0 基索引（含）")
    beat_number_start: int = Field(..., description="片段起始拍号（1 基，含）")
    beat_number_end: int = Field(..., description="片段结束拍号（1 基，含）")
    ref_start_time: float | None
    ref_end_time: float | None
    missed_count: int
    inserted_count: int
    over_threshold_count: int = Field(..., description="超阈值偏差拍数")
    early_count: int
    late_count: int
    issue_count: int
    max_abs_deviation: float | None
    summary: str = Field(..., description="问题摘要（中文）")
    issues: list[ReviewIssue]


class ReviewSegmentsOut(BaseModel):
    job_id: int
    parameters: dict[str, Any]
    segment_count: int = Field(..., description="复核片段数量")
    total_missed_count: int
    total_inserted_count: int
    total_over_threshold_count: int
    segments: list[ReviewSegment] = Field(
        default_factory=list, description="按参考拍顺序排列的复核片段"
    )


class JobSummary(BaseModel):
    id: int
    name: str | None
    status: str
    created_at: str
    aligned_at: str | None
    ref_beat_count: int
    perf_onset_count: int


class JobOut(BaseModel):
    id: int
    name: str | None
    reference_beats: list[float]
    performance_onsets: list[float]
    anchors: list[AnchorPair] = Field(
        default_factory=list, description="任务创建时指定的锚点硬约束"
    )
    params: dict[str, Any]
    status: str
    error: str | None
    created_at: str
    aligned_at: str | None
    result: AlignmentResult | None = None


# ---- 跨任务合奏一致性 POST /ensembles/consensus ----


class ConsensusRequest(BaseModel):
    job_ids: list[int] = Field(
        ...,
        min_length=3,
        description=(
            "参与合奏一致性分析的已完成任务 ID（每个任务一条演奏轨），"
            "至少 3 个；不可重复，且所有任务的参考节拍网格必须完全相同"
        ),
    )
    deviation_threshold: float = Field(
        ..., gt=0, description="秒级异常阈值：校正时间相对合奏基准 |偏差| 严格大于该值报异常"
    )


class EnsembleMissedBeat(BaseModel):
    ref_index: int
    beat_number: int
    ref_time: float


class EnsembleTrack(BaseModel):
    job_id: int = Field(..., description="该演奏轨对应的任务 ID")
    name: str | None
    delay: float | None = Field(
        ...,
        description=(
            "估计出的固定录音延迟（秒）：匹配拍 (perf_time−ref_time) 的中位数；"
            "无匹配拍时为 null"
        ),
    )
    matched_beat_count: int
    missed_beat_count: int
    missed_beats: list[EnsembleMissedBeat] = Field(
        default_factory=list, description="该轨漏拍清单（逐轨列出）"
    )


class EnsembleOutlier(BaseModel):
    job_id: int
    corrected_time: float = Field(..., description="该轨该拍校正后的演奏时间（秒）")
    deviation: float = Field(
        ..., description="相对合奏基准的偏差（秒，校正时间−基准）；负=抢拍，正=拖拍"
    )
    status: BeatStatus


class EnsembleBeat(BaseModel):
    ref_index: int
    beat_number: int
    ref_time: float
    decidable: bool = Field(
        ..., description="匹配轨数 ≥ 3 时为 true；false=覆盖不足，不可判定且不报异常"
    )
    matched_track_count: int
    consensus_time: float | None = Field(
        ..., description="合奏基准（各轨校正时间的中位数，秒）；不可判定时为 null"
    )
    matched_job_ids: list[int] = Field(
        ..., description="匹配该拍的轨对应任务 ID（按提交顺序）"
    )
    missing_job_ids: list[int] = Field(
        ..., description="漏该拍（未匹配）的轨对应任务 ID（按提交顺序）"
    )
    outlier_count: int
    outliers: list[EnsembleOutlier] = Field(
        default_factory=list,
        description="偏离合奏基准超过阈值的轨（任务 ID + 偏差）；不可判定拍恒为空",
    )


# ---- 多轨剪辑方案 POST /takes/comp-plan ----


class CompPlanRequest(BaseModel):
    job_ids: list[int] = Field(
        ...,
        min_length=2,
        description=(
            "参与剪辑方案的已完成任务 ID（每个任务对应一条录音 / take），"
            "至少 2 个；不可重复，且所有任务的参考节拍网格必须完全相同"
        ),
    )
    beats_per_measure: int = Field(
        ..., ge=1, description="每小节拍数（按参考拍划小节，末尾不足整小节也作为一节）"
    )
    switch_cost: float = Field(
        ..., ge=0, description="相邻小节选自不同任务（换轨）时的非负代价（秒）"
    )


class CompMeasureCandidate(BaseModel):
    job_id: int = Field(..., description="候选录音对应的任务 ID")
    mean_absolute_deviation: float = Field(
        ..., description="该轨本小节匹配拍的平均绝对偏差（秒），即选用代价"
    )


class CompMeasure(BaseModel):
    measure_index: int = Field(..., description="小节序号（0 基）")
    measure_number: int = Field(..., description="小节序号（1 基）")
    ref_start_index: int = Field(..., description="小节首拍的参考拍 0 基索引")
    ref_end_index: int = Field(..., description="小节末拍的参考拍 0 基索引（含）")
    beat_number_start: int = Field(..., description="小节首拍拍号（1 基）")
    beat_number_end: int = Field(..., description="小节末拍拍号（1 基，含）")
    beat_count: int
    job_id: int = Field(..., description="本小节选中的录音任务 ID")
    mean_absolute_deviation: float = Field(
        ..., description="本小节选中录音的选用代价（匹配拍平均绝对偏差，秒）"
    )
    source_first_match_time: float = Field(
        ..., description="选中录音在本小节首个匹配音的时间（秒，演奏时间轴）"
    )
    source_last_match_time: float = Field(
        ..., description="选中录音在本小节末个匹配音的时间（秒，演奏时间轴）"
    )
    switched_from_previous: bool = Field(
        ..., description="相对前一小节是否换轨（首小节恒为 false）"
    )
    candidates: list[CompMeasureCandidate] = Field(
        ..., description="本小节全部候选录音（完整匹配该小节全部拍的轨，按请求顺序）"
    )


class CompSegment(BaseModel):
    segment_index: int
    job_id: int = Field(..., description="本片段来源录音的任务 ID")
    name: str | None
    measure_start_index: int = Field(..., description="片段首小节的 0 基序号")
    measure_end_index: int = Field(..., description="片段末小节的 0 基序号（含）")
    measure_number_start: int = Field(..., description="片段首小节序号（1 基）")
    measure_number_end: int = Field(..., description="片段末小节序号（1 基，含）")
    measure_count: int = Field(..., description="片段覆盖的小节数")
    ref_start_index: int = Field(..., description="片段首拍的参考拍 0 基索引")
    ref_end_index: int = Field(..., description="片段末拍的参考拍 0 基索引（含）")
    beat_number_start: int = Field(..., description="片段首拍拍号（1 基）")
    beat_number_end: int = Field(..., description="片段末拍拍号（1 基，含）")
    beat_count: int = Field(..., description="片段覆盖的参考拍数")
    source_start_time: float = Field(
        ..., description="来源录音片段首个匹配音时间（秒，演奏时间轴）"
    )
    source_end_time: float = Field(
        ..., description="来源录音片段末个匹配音时间（秒，演奏时间轴）"
    )


class CompPlanOut(BaseModel):
    parameters: dict[str, Any]
    job_ids: list[int] = Field(..., description="本次参与的任务 ID（按请求顺序）")
    reference_beats: list[float] = Field(
        ..., description="所有任务共享的参考节拍网格（秒）"
    )
    reference_beat_count: int
    take_count: int = Field(..., description="参与的录音（任务）数量")
    measure_count: int = Field(..., description="按参考拍划出的小节数（含末尾不足整小节）")
    segment_count: int = Field(..., description="合并连续同源小节后的片段数量")
    switch_count: int = Field(..., description="最优方案中的换轨次数")
    total_cost: float = Field(
        ..., description="最优方案总代价（各小节选用代价 + 换轨代价之和，秒）"
    )
    measures: list[CompMeasure] = Field(
        ..., description="逐小节选轨明细（按小节顺序）"
    )
    segments: list[CompSegment] = Field(
        ..., description="合并连续同源小节后的剪辑片段（按参考拍顺序）"
    )


class EnsembleConsensusOut(BaseModel):
    parameters: dict[str, Any]
    job_ids: list[int] = Field(..., description="本次参与的任务 ID（按提交顺序）")
    reference_beats: list[float] = Field(
        ..., description="所有任务共享的参考节拍网格（秒）"
    )
    reference_beat_count: int
    track_count: int
    tracks: list[EnsembleTrack] = Field(
        ..., description="各轨结果：固定录音延迟、匹配拍数与逐轨漏拍"
    )
    beat_count: int
    decidable_beat_count: int = Field(..., description="可判定拍数（≥3 轨匹配）")
    undecidable_beat_count: int = Field(
        ..., description="不可判定拍数（匹配轨不足 3，不报异常）"
    )
    outlier_count: int
    beats: list[EnsembleBeat] = Field(
        ..., description="按参考拍顺序排列的每拍合奏结果"
    )
