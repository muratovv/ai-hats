# IaC Tools Development Rules

## 1. Documentation First
Before generating any IaC config, check current official documentation for the tool and its providers. Do NOT rely on memory — APIs, parameter names, and block structures change between versions.

## 2. No Hardcoded Versions
Do NOT hardcode OS image versions, ISOs, or container tags without verifying current availability. When referencing remote resources (ISO URLs, Docker image tags), verify they exist first.

## 3. Environment Awareness
When generating IaC configs, consult the project's environment documentation for: API endpoints, storage names, network bridges, TLS settings, node names, and other environment-specific details.
