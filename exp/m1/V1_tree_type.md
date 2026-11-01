# 结构摘要 乙

- era=2020以前 ∧ source.kind=journal ∧ type=article ｜ 2803 项
- era=2020以前 ∧ evidence_status=仓储 ∧ type=article ∧ ¬(source.kind=journal) ｜ 30 项
- era=2020以前 ∧ evidence_status=未知 ∧ type=article ∧ ¬(evidence_status=仓储 ∨ source.kind=journal) ｜ 8 项
- era=2020以前 ∧ type=article ∧ ¬(evidence_status=仓储 ∨ evidence_status=未知 ∨ source.kind=journal) ｜ 6 项
- evidence_status=同行评审 ∧ type=article ∧ ¬(era=2020以前) ｜ 214 项
- evidence_status=仓储 ∧ type=article ∧ ¬(era=2020以前 ∨ evidence_status=同行评审) ｜ 5 项
- type=article ∧ ¬(era=2020以前 ∨ evidence_status=仓储 ∨ evidence_status=同行评审) ｜ 2 项
