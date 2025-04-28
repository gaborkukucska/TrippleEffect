<!-- # START OF FILE helperfiles/DEVELOPMENT_RULES.md -->
# Development Rules
During development please follow these rules.

*   Follow a phased implementation as outlined in the `helperfiles/PROJECT_PLAN.md` file.
*   Maintain `README.md`, `helperfiles/PROJECT_PLAN.md` (update status) and `helperfiles/FUNCTIONS_INDEX.md`, updating them at every milestone.
*   Write the location and name of every file in its first line like `<!-- # START OF FILE subfolder/file_name.extension -->`.
*   Follow the user's specified interaction model:
    *   Analyze context fully before suggesting changes.
    *   Whenever available use the log files to find clues. These files might be very large so first search them for warnings, errors or other specific strings, then use their time stamps to find more relating debug logs.
    *   Provide complete file contents for modification.
    *   Wait for confirmation before proceeding to the next file.
    *   Maintain code consistency.

## Known Issues / Workarounds (as of v2.25)

*   **Taskwarrior UDA Issues:** Setting User Defined Attributes (like `assignee`) via the Taskwarrior CLI `add` or `modify` commands within `tasklib`'s `execute_command` has proven unreliable (often corrupting the description or failing silently). The current workaround for task assignment uses tags (`+agent_id`).
*   **PM Agent Tool Usage:** The Project Manager agent may attempt multiple tool calls in one response, violating framework rules. Its system prompt needs careful wording to enforce sequential execution (under investigation).
*   **Rate Limiting:** External API rate limits (e.g., OpenRouter free tier) can halt agent execution. Ensure adequate limits or configure alternative providers/models in `.env` or `config.yaml`.

# Example Mermaid graph
**Mermaid Syntax:** Remember that Mermaid diagrams used in Markdown (like in `README.md` or `PROJECT_PLAN.md`) are sensitive to inline comments. Use `%% Comment Text` on a **separate line** for comments within diagram definitions, not after a statement on the same line (e.g., `NodeA --> NodeB; %% This might break`).

```mermaid
graph TD %% Or LR, etc. Choose layout direction

    %% --- Node Definitions ---
    %% Use simple alphanumeric IDs. Use "" for text. Use <br> for line breaks.
    %% Avoid [], (), {}, ;, or other special characters within the quoted node text unless escaped or known to work.
    Node_A["Node A Text <br>Line 2"]
    Node_B["Node B Text"]

    %% --- Subgraph Definitions ---
    subgraph Subgraph_Title ["Optional Display Title"]
        %% Indentation is optional but helps readability
        direction LR %% Optional: Direction within subgraph
        Subgraph_Node1["Node 1 in Subgraph"]
        Subgraph_Node2["Node 2 in Subgraph"]
    end %% End of subgraph

    %% --- Connections ---
    %% Use -- --> for standard links. Add labels in "".
    Node_A -- "Connects to" --> Node_B;
    Node_B -- --> Subgraph_Node1; %% Connection without label
    Subgraph_Node1 -- --> Subgraph_Node2;

    %% --- Comments ---
    %% CRITICAL: Comments MUST be on their own separate line.
    %% Do NOT place comments at the end of node or connection lines.

    %% Good Comment Style:
    %% This explains the next section.
    Node_X --> Node_Y;

    %% BAD Comment Style (DO NOT DO THIS):
    %% Node_X --> Node_Y; %% This comment will break the diagram!
```
