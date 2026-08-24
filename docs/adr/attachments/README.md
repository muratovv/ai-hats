# ADR attachments

Material an ADR leans on that is neither the decision itself nor user documentation:
working context an agent loads before touching the code an ADR governs, measured
baselines a decision was taken against, and corpora a decision cites. An attachment is
owned by the ADR that references it — it is reached from that ADR's References section,
it may be rewritten whenever the work it describes moves, and it carries no promise to
a reader outside the epic. What does **not** belong here: the decision and its
rationale (that is the ADR, in `docs/adr/`), anything a user or an integrator is meant
to read (that is a companion doc, catalogued in `docs/INDEX.md`), and transient
per-card state (that lives on the tracker card, and ADRs cite it there).
