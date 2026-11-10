# 0001 · Revision continuity

Retain both physical revisions until the new projection is searchable. PostgreSQL activates a projection only when its lease token, generation and desired revision match. Apply eligibility before ranking and limiting candidates. Withdrawal excludes the old revision even when a delayed worker later finishes indexing.

The witness retains revision 7 while revision 8 is prepared, then switches eligibility after activation. It also tests a late worker after withdrawal and enough obsolete projections to fill a candidate list. Cleanup and PDF generation activation have separate acceptance work. [Verification](../../docs/verification/witness-increments.md)
