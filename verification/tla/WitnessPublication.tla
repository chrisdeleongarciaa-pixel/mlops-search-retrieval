----------------------------- MODULE WitnessPublication -----------------------------
EXTENDS Naturals, FiniteSets
CONSTANT MaxRevision
VARIABLES desired, published, lastWithdrawal, withdrawn, running, indexed
vars == <<desired, published, lastWithdrawal, withdrawn, running, indexed>>

Init == /\ desired = 0 /\ published = 0 /\ lastWithdrawal = 0
        /\ withdrawn = FALSE /\ running = {} /\ indexed = {}

Accept == /\ desired < MaxRevision
          /\ desired' = desired + 1 /\ withdrawn' = FALSE
          /\ UNCHANGED <<published, lastWithdrawal, running, indexed>>

Withdraw == /\ desired < MaxRevision
            /\ desired' = desired + 1 /\ lastWithdrawal' = desired + 1
            /\ withdrawn' = TRUE /\ UNCHANGED <<published, running, indexed>>

Claim == /\ desired > 0 /\ ~withdrawn /\ desired \notin running
         /\ running' = running \cup {desired}
         /\ UNCHANGED <<desired, published, lastWithdrawal, withdrawn, indexed>>

Feed == \E r \in running:
    /\ r \notin indexed /\ indexed' = indexed \cup {r}
    /\ UNCHANGED <<desired, published, lastWithdrawal, withdrawn, running>>

Finish == \E r \in running \cap indexed:
    /\ published' = IF r = desired /\ ~withdrawn THEN r ELSE published
    /\ running' = running \ {r}
    /\ UNCHANGED <<desired, lastWithdrawal, withdrawn, indexed>>

Crash == \E r \in running:
    /\ running' = running \ {r}
    /\ UNCHANGED <<desired, published, lastWithdrawal, withdrawn, indexed>>

Next == Accept \/ Withdraw \/ Claim \/ Feed \/ Finish \/ Crash
Eligible == IF published > lastWithdrawal THEN {published} ELSE {}
Invariant == /\ published <= desired
             /\ Eligible \subseteq indexed
             /\ withdrawn => Eligible = {}
             /\ \A r \in Eligible: r > lastWithdrawal
=============================================================================
