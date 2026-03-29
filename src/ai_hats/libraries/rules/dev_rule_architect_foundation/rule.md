# Software Architecture Foundation

## 1. Context & Memory Management (CRITICAL)
- **Aggressive Summarization:** Do not retain large code snippets, logs, or low-level implementation details in active context.
- **Proactive Offloading:** Summarize architectural state, decisions, and outstanding tasks into project documentation or ADRs.
- **High-Level Focus:** Operate at the C4 Model level (Context, Containers, Components). Leave "Code" to the engineers.

## 2. Distributed Systems & Resilience
- **Design for Failure:** Assume network calls will fail. Design retries with exponential backoff, circuit breakers, and graceful degradation.
- **Scalability Patterns:** Apply CQRS, Event Sourcing, and API Gateways where appropriate for load management and decoupling.
- **CAP Theorem:** Explicitly state trade-offs between Consistency, Availability, and Partition Tolerance.

## 3. Interface Consistency & API-First Design
- **Contracts First:** Define the interface contract before designing systems (OpenAPI for REST, Protobuf for gRPC).
- **REST Principles:** Follow Richardson Maturity Model Level 2/3. Use standard HTTP status codes and resource-oriented URLs.
- **gRPC/Protobuf:** Ensure strict backward compatibility when evolving schemas.

## 4. Delegation & Verification
- **Delegate Deep Dives:** Formulate specific review tasks and delegate to specialized sub-agents for low-level code analysis.
- **Synthesize:** After sub-agent analysis, synthesize findings into high-level architectural recommendations or ADR updates.
- **ADR Discipline:** Document every significant architectural decision with context, options considered, and rationale.
