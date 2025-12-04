# Context Profile for TradeBotServer

## Overview

The `.cursor/context_profile.json` file contains a comprehensive database of common problems, fixes, and patterns in the tradeBotServer codebase. This profile helps AI assistants quickly understand and debug issues without repeating past mistakes.

## Structure

### Common Problems
Each problem entry includes:
- **Problem description**: What the issue is
- **Symptoms**: How to identify the problem
- **Root causes**: Why it happens
- **Solutions**: How to fix it (with file locations and code patterns)
- **Prevention**: How to avoid it in the future
- **Related files**: Files involved in the fix

### Syntax/Linter Errors
Common error patterns with:
- Error message
- Common locations
- Fix approach
- Prevention tips

### Code Patterns
- **Avoid**: Patterns that cause problems
- **Prefer**: Best practices and recommended patterns

### Best Practices
Organized by category:
- Token management
- Async programming
- Error handling
- API integration
- Strategy development

### File-Specific Notes
Important notes about specific files:
- Critical methods and their sync/async nature
- Common issues per file
- Required methods/interfaces

## How to Use

### For AI Assistants
When debugging issues:
1. Check `common_problems` for similar issues
2. Review `syntax_linter_errors` for common mistakes
3. Check `file_specific_notes` for file-specific patterns
4. Follow `best_practices` when making changes

### For Developers
When fixing issues:
1. Document the problem in `common_problems`
2. Add prevention tips to avoid recurrence
3. Update `recent_fixes` with the fix details
4. Update `best_practices` if new patterns emerge

## Updating the Profile

### When to Update
- After fixing a recurring issue
- When discovering a new common problem
- When establishing a new best practice
- When finding a new syntax/linter error pattern

### How to Update
1. Add entry to appropriate section
2. Include all relevant details (symptoms, causes, solutions)
3. Add prevention tips
4. Update `recent_fixes` with date and details
5. Update `last_updated` field

## Current Coverage

### Problems Documented
- ✅ JWT token expiration and 500 errors
- ✅ Async/await syntax errors
- ✅ AccountTracker missing methods
- ✅ SignalR connection spam
- ✅ Strategy status KeyError
- ✅ Excessive API calls
- ✅ Chart real-time updates

### Error Patterns Documented
- ✅ await outside async function
- ✅ Missing imports
- ✅ Type errors (not callable)
- ✅ Attribute errors
- ✅ Key errors

## Maintenance

This profile should be updated regularly as new issues are discovered and fixed. The goal is to build a comprehensive knowledge base that prevents repeated mistakes and speeds up debugging.

## Related Documentation

See the `docs/` folder for detailed documentation on specific fixes:
- `JWT_AUTO_REFRESH_FIX_2025-12-02.md` - JWT token management
- `TROUBLESHOOTING.md` - General troubleshooting guide
- `AUTOMATION_FIXES_APPLIED.md` - Strategy automation fixes
- `SIGNALR_CONNECTION_FIX_2025-12-02.md` - SignalR connection issues
- `CHART_FIXES_2025-12-02.md` - Chart update issues

