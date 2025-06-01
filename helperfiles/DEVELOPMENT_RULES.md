<!-- # START OF FILE helperfiles/DEVELOPMENT_RULES.md -->
# Development Rules
During development please follow these rules.

*   Follow a phased implementation as outlined in the `helperfiles/PROJECT_PLAN.md` file.
*   Maintain `README.md`, `helperfiles/PROJECT_PLAN.md` (update status) and `helperfiles/FUNCTIONS_INDEX.md`, updating them at every milestone.
*   Write the location and name of every file in its first line like `<!-- # START OF FILE subfolder/file_name.extension -->`.
*   Follow the user's specified interaction model:
    *   Analyze context fully before suggesting changes.
    *   Whenever available use the log files to find clues. **These files might be very large so first search them for warnings, errors or other specific strings, then use the time stamps to find more detailed debug logs around those times.**
    *   Maintain code consistency.

# **IMPORTANT**
DO NOT confuse your own framework with the TrippleEffect framework that you are working on! The code base you are helping to develop is NOT the one you're running in!!!
