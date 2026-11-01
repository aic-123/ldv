# 结构摘要

- source.kind=journal ∧ type=article ｜ 3017 项
- evidence_status=仓储 ∧ type=article ∧ ¬(source.kind=journal) ｜ 35 项
- evidence_status=同行评审 ∧ type=article ∧ ¬(evidence_status=仓储 ∨ source.kind=journal) ｜ 6 项
- type=article ∧ ¬(evidence_status=仓储 ∨ evidence_status=同行评审 ∨ source.kind=journal) ｜ 10 项
- evidence_status=同行评审 ∧ type=review ∧ ¬(type=article) ｜ 572 项
- evidence_status=仓储 ∧ type=review ∧ ¬(evidence_status=同行评审 ∨ type=article) ｜ 2 项
- type=review ∧ ¬(evidence_status=仓储 ∨ evidence_status=同行评审 ∨ type=article) ｜ 1 项
- evidence_status=同行评审 ∧ source.kind=conference ∧ type=conference-paper ∧ ¬(type=article ∨ type=review) ｜ 147 项
- evidence_status=同行评审 ∧ source.kind=conference ∧ type=conference-abstract ∧ ¬(type=article ∨ type=conference-paper ∨ type=review) ｜ 1 项
- evidence_status=同行评审 ∧ source.kind=conference ∧ ¬(type=article ∨ type=conference-abstract ∨ type=conference-paper ∨ type=review) ｜ 1 项
- evidence_status=同行评审 ∧ source.kind=book series ∧ type=conference-paper ∧ ¬(source.kind=conference ∨ type=article ∨ type=review) ｜ 23 项
- evidence_status=同行评审 ∧ source.kind=book series ∧ type=book ∧ ¬(source.kind=conference ∨ type=article ∨ type=conference-paper ∨ type=review) ｜ 2 项
