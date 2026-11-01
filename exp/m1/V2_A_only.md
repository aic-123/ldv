# 结构摘要 一

- source.kind=journal ∧ type=article ｜ 2331 项
- evidence_status=仓储 ∧ type=article ∧ ¬(source.kind=journal) ｜ 22 项
- evidence_status=未知 ∧ type=article ∧ ¬(evidence_status=仓储 ∨ source.kind=journal) ｜ 6 项
- type=article ∧ ¬(evidence_status=仓储 ∨ evidence_status=未知 ∨ source.kind=journal) ｜ 1 项
- evidence_status=同行评审 ∧ type=review ∧ ¬(type=article) ｜ 464 项
- evidence_status=仓储 ∧ type=review ∧ ¬(evidence_status=同行评审 ∨ type=article) ｜ 1 项
- type=review ∧ ¬(evidence_status=仓储 ∨ evidence_status=同行评审 ∨ type=article) ｜ 1 项
- evidence_status=同行评审 ∧ source.kind=conference ∧ type=conference-abstract ∧ ¬(type=article ∨ type=review) ｜ 1 项
- evidence_status=同行评审 ∧ source.kind=conference ∧ ¬(type=article ∨ type=conference-abstract ∨ type=review) ｜ 60 项
- evidence_status=同行评审 ∧ source.kind=book series ∧ type=conference-paper ∧ ¬(source.kind=conference ∨ type=article ∨ type=review) ｜ 10 项
- evidence_status=同行评审 ∧ source.kind=book series ∧ type=book-chapter ∧ ¬(source.kind=conference ∨ type=article ∨ type=conference-paper ∨ type=review) ｜ 2 项
- evidence_status=同行评审 ∧ source.kind=book series ∧ ¬(source.kind=conference ∨ type=article ∨ type=book-chapter ∨ type=conference-paper ∨ type=review) ｜ 1 项
