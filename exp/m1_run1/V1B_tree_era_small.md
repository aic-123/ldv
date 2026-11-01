# 结构摘要 甲

- era=2020及以后 ∧ source.kind=journal ∧ type=article ｜ 2 项
- evidence_status=同行评审 ∧ source.kind=conference ∧ ¬(era=2020以前 ∨ type=article ∨ type=review) ｜ 1 项
- evidence_status=同行评审 ∧ source.kind=book series ∧ ¬(era=2020以前 ∨ source.kind=conference ∨ type=article ∨ type=conference-paper ∨ type=review) ｜ 1 项
- evidence_status=同行评审 ∧ ¬(era=2020以前 ∨ source.kind=book series ∨ source.kind=conference ∨ type=article ∨ type=book-review ∨ type=editorial ∨ type=review) ｜ 1 项
- type=preprint ∧ ¬(era=2020以前 ∨ evidence_status=同行评审 ∨ type=article ∨ type=review) ｜ 1 项
- era=2020及以后 ∧ type=book-chapter ∧ ¬(evidence_status=同行评审 ∨ type=article ∨ type=preprint ∨ type=review) ｜ 5 项
- era=2020及以后 ∧ source.kind=repository ∧ ¬(evidence_status=同行评审 ∨ type=article ∨ type=book-chapter ∨ type=preprint ∨ type=review) ｜ 1 项
- era=2020及以后 ∧ ¬(evidence_status=同行评审 ∨ source.kind=repository ∨ type=article ∨ type=book-chapter ∨ type=preprint ∨ type=review) ｜ 4 项
