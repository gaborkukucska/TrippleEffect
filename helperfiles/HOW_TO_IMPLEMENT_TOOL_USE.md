<!-- important parts of the files referenced in this file are in the `CLINE_REFERENCE_FILES.md` file. -->
# Cline Tool Use

Here are the specific notes detailing the implementation of Cline's tool use handling:

**Cline Tool Use Handling Mechanism**

Cline's ability to use tools relies on a two-part mechanism:

1.  **System Prompt Instructions:** Clearly instructing the Large Language Model (LLM) on *how* to request tool usage.
2.  **Response Stream Parsing:** Analyzing the LLM's text output stream in real-time to detect and extract these tool usage requests.

**1. System Prompt Instructions (`src/core/prompts/system.ts`)**

The system prompt is critical for setting the rules and format for tool usage. Key implementation points derived from `system.ts`:

*   **Explicit Tool Format:** The prompt explicitly defines the required XML-style format:
    ```xml
    <tool_name>
    <parameter1_name>value1</parameter1_name>
    <parameter2_name>value2</parameter2_name>
    ...
    </tool_name>
    ```
    This strict format is essential for the parser to reliably detect tool calls.
*   **Tool Definition:** Each available tool (e.g., `execute_command`, `read_file`, `write_to_file`, `replace_in_file`, `use_mcp_tool`, etc.) is listed with:
    *   A clear `Description` explaining its purpose and when to use it.
    *   A `Parameters` section listing *all* possible parameters (both required and optional). Parameter names must match exactly those defined in `src/core/assistant-message/index.ts` (`toolParamNames` array).
    *   A `Usage` example showing the correct XML structure for that specific tool.
*   **Tool Use Guidelines:** The prompt enforces specific rules:
    *   **One Tool Per Message:** The LLM is instructed to use only *one* tool per message it sends.
    *   **Tools at the End:** All tool calls must be placed at the *very end* of the assistant's message, after any explanatory text.
    *   **Wait for Result:** The LLM must wait for the user's response (which contains the tool result) before proceeding or using another tool.
    *   **Use `<thinking>` Tags:** While not a tool itself, the prompt encourages using `<thinking>` tags for internal reasoning *before* generating the response text and tool call. The parser needs to handle (ignore) these tags correctly.
    *   **Parameter Tags:** Values for parameters *must* be enclosed within their corresponding `<parameter_name>` tags.
*   **Dynamic Content (MCP):** The prompt dynamically includes details about connected MCP servers, including their names, available tools (with input schemas), and resources. This allows the LLM to request MCP tools using the correct `server_name` and `tool_name`.
*   **File Editing Strategy:** Specific instructions are given for `write_to_file` (provide *complete* content) and `replace_in_file` (use precise SEARCH/REPLACE blocks, handle multi-line content correctly, keep blocks concise). The parser for `replace_in_file` needs to handle the multi-line `diff` parameter content correctly.

**2. Response Stream Parsing (`src/core/assistant-message/parse-assistant-message.ts`)**

The `parseAssistantMessage` function processes the incoming text stream from the LLM character by character to identify and structure tool uses.

*   **Streaming Nature:** The function is designed to be called repeatedly as new chunks of text arrive from the LLM stream. It handles partial tags and content that might span multiple chunks.
*   **State Machine:** It operates like a state machine, tracking its current context:
    *   `currentTextContent`: Accumulating regular text.
    *   `currentToolUse`: Inside a `<tool_name>...</tool_name>` block.
    *   `currentParamName`: Inside a `<parameter_name>...</parameter_name>` block within a `currentToolUse`.
*   **Tag Detection:**
    *   It uses `accumulator.endsWith('<tag_name>')` to detect the start of tool uses and parameters, checking against the predefined `toolUseNames` and `toolParamNames` arrays from `src/core/assistant-message/index.ts`.
    *   It uses `accumulator.endsWith('</tag_name>')` to detect the end of parameters and tool uses.
*   **Output Structure:** It outputs an array of `AssistantMessageContent` objects (`TextContent` or `ToolUse`).
*   **Text vs. Tool Differentiation:**
    *   Any text encountered *before* a recognized `<tool_name>` tag starts is treated as `TextContent`.
    *   Once a `<tool_name>` tag is detected, subsequent text (until `</tool_name>`) is processed as part of that tool use, including parameter extraction.
*   **Parameter Extraction:**
    *   When a `<parameter_name>` tag is detected inside a `currentToolUse`, the state shifts to capture the parameter value.
    *   The text between `<parameter_name>` and `</parameter_name>` is captured, trimmed, and stored in the `currentToolUse.params` object with the `parameter_name` as the key.
    *   Special handling exists for the `write_to_file`'s `<content>` parameter to correctly capture multi-line content, even if it contains `</content>`-like strings, by looking for the *last* closing tag.
*   **Partial State Handling:**
    *   If the input `assistantMessage` string ends mid-tag (e.g., `<execu`, `</comma`), the parser recognizes this state.
    *   If the stream ends (indicated by the calling code, not the parser itself) while `currentToolUse` or `currentParamName` is active, the corresponding `ToolUse` or `TextContent` block in the output array is marked with `partial: true`.
    *   The parser logic explicitly handles cleaning up potentially incomplete marker tags at the *very end* of a chunk (`<<<<<<<`, `=======`, `>>>>>>>`) to prevent them from being rendered partially in the UI if the stream cuts off mid-marker. It also removes partial XML tags like `<tool_n` or `</param` at the end of text content chunks.
*   **Output Generation:**
    *   When regular text is accumulating and a `<tool_name>` tag is detected, the accumulated `currentTextContent` is finalized (`partial: false`), potentially trimmed of the partial tag start (`<tool_`), added to the `contentBlocks` array, and `currentTextContent` is reset.
    *   When a `</parameter_name>` tag is detected, the parameter value is stored, and `currentParamName` is reset.
    *   When a `</tool_name>` tag is detected, the `currentToolUse` is finalized (`partial: false`), added to `contentBlocks`, and `currentToolUse` is reset.
    *   If the stream ends, any remaining `currentTextContent` or `currentToolUse` (which might be partially filled) is added to `contentBlocks` with `partial: true`.

**3. Handling Parsed Output (Responsibility of the Caller, e.g., `Task.ts`)**

*   The code calling `parseAssistantMessage` (like the `Task` class) receives the array of `AssistantMessageContent` blocks.
*   It iterates through these blocks:
    *   `TextContent`: Displayed to the user, potentially streamed if `partial: true`.
    *   `ToolUse`:
        *   If `partial: true`, it might update a placeholder UI indicating the tool being used.
        *   If `partial: false`, the tool request is considered complete. The caller then:
            *   Validates if required parameters are present (semantic validation, not done by the parser).
            *   Checks auto-approval settings.
            *   Presents the tool request to the user for approval (if needed) via an `ask` message.
            *   Executes the tool upon approval.
            *   Formats the tool's result into a `user` role message to send back to the LLM in the *next* request.

**Key Implementation Considerations:**

*   **Strict Formatting:** The parser heavily relies on the exact XML format defined in the system prompt. Any deviation by the LLM will likely cause parsing errors or incorrect tool extraction.
*   **Defined Names:** The parser explicitly checks against the `toolUseNames` and `toolParamNames` arrays. Only tools and parameters defined there will be recognized.
*   **State Management:** The parser needs careful state management (`inSearch`, `inReplace` are actually from `diff.ts`, the relevant states in `parse-assistant-message.ts` are `currentToolUse`, `currentParamName`) to handle nested structures (parameters within tools) correctly during streaming.
*   **Error Handling:** The *parser* primarily handles syntax/format issues. The *caller* must handle semantic issues (e.g., missing required parameters, invalid parameter values, tool execution errors).
*   **Order Matters:** The system prompt mandates text first, then tools. The parser assumes this structure.

By combining these system prompt instructions with the streaming parser, Cline can reliably interpret the LLM's intentions to use tools and extract the necessary information to execute them.
