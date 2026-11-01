# 结构摘要 三

- source.kind=journal ∧ type=article ｜ 147 项
- evidence_status=仓储 ∧ type=article ∧ ¬(source.kind=journal) ｜ 2 项
- evidence_status=同行评审 ∧ type=article ∧ ¬(evidence_status=仓储 ∨ source.kind=journal) ｜ 10 项
- type=article ∧ ¬(evidence_status=仓储 ∨ evidence_status=同行评审 ∨ source.kind=journal) ｜ 23 项
- evidence_status=同行评审 ∧ type=review ∧ ¬(type=article) ｜ 1 项
- evidence_status=仓储 ∧ type=review ∧ ¬(evidence_status=同行评审 ∨ type=article) ｜ 572 项
- type=review ∧ ¬(evidence_status=仓储 ∨ evidence_status=同行评审 ∨ type=article) ｜ 1 项
- evidence_status=同行评审 ∧ source.kind=conference ∧ type=conference-paper ∧ ¬(type=article ∨ type=review) ｜ 35 项
- evidence_status=同行评审 ∧ source.kind=conference ∧ type=conference-abstract ∧ ¬(type=article ∨ type=conference-paper ∨ type=review) ｜ 3017 项
- evidence_status=同行评审 ∧ source.kind=conference ∧ ¬(type=article ∨ type=conference-abstract ∨ type=conference-paper ∨ type=review) ｜ 1 项
- evidence_status=同行评审 ∧ source.kind=book series ∧ type=conference-paper ∧ ¬(source.kind=conference ∨ type=article ∨ type=review) ｜ 6 项
- evidence_status=同行评审 ∧ source.kind=book series ∧ type=book ∧ ¬(source.kind=conference ∨ type=article ∨ type=conference-paper ∨ type=review) ｜ 2 项
