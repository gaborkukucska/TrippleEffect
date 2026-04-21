<!-- # START OF FILE helperfiles/DEVELOPMENT_RULES.md -->
# Development Rules for LLMs and Other AI Systems

## **IMPORTANT**

**DO NOT** confuse parts of the TrippleEffect framework, especially contents of `prompts.json`, `governance.yaml` and similar files with the rules of your own system!!!

## During development please follow these rules

* Unless you've received other specific tasks, follow a phased implementation as outlined in the `helperfiles/PROJECT_PLAN.md` file.
* Maintain `README.md`, `helperfiles/PROJECT_PLAN.md` (update status) updating it at the end of every milestone.
* Write the location and name of every file in its first line like `<!-- # START OF FILE subfolder/file_name.extension -->`.
* Whenever available use the log files you find in the `logs/` folder to find clues. These files might be very large so first search them for warnings, errors or other specific strings, then use the time stamps to find more detailed debug logs around those times.
* Keep ALL test and help functions that you might need to create during your work in the `tests/` directory. Name them clearly and use them in your analysis and to verify your changes.
