# Missy Black Bot — checkpoint 2026-11-01

## Verified release

- Repository: EvenBlack/missy-black-bot, main.
- Latest application release: 219eb9bd939444eaa920e3b2233d2009847cc425 (PR #20).
- Assistant workspace release: PR #19, e5570045fa3ae6ab8e2275f7e149e14968975ee8.
- Render service: missy-black-bot, srv-dbh73mnf3r2c73brjr4g.
- Render deployment: dep-dbjjvpnlk1mc739fqih0, live.
- Runtime logs confirm Drive initialization, archives ready, scheduler start and Telegram polling connection restored after deployment overlap.
- Latest automated validation: 166 Python tests passed, including 13 natural-command checks. Existing Node storage checks passed before the workspace release; storage protocol did not change in PRs #19/#20.

## Implemented capabilities

- Two conversational styles selected by addressing the bot by name; configured account identity and grammatical address.
- Owner-scoped conversation history, explicit preferences, facts, history recall and voice memories on Google Drive.
- Web/image search, original image archive, primary character image and local image editing.
- Voice transcription and audio/transcript archive; photo descriptions and note search.
- EPUB/TXT library, indexed excerpts, book discussion and public-domain catalog search.
- Explicit Telegram group registration, draft/confirmed/scheduled broadcasts, templates, polls, staged copy/forward, own-publication controls and keyword monitoring.
- Durable private reminders, weekday recurrence, rescheduling, cancellation and emergency stop; uncertain delivery is not replayed automatically.
- Public site/RSS monitoring, summaries, deeper multi-source web research and saved research reports.
- Telegram menu, personal tasks/projects, editable active facts/preferences, enhanced lexical history retrieval.
- Internal calendar, overlap warnings, ICS import/export, opt-in daily briefings and health checks.
- PDF/DOCX extraction and Ukrainian/English OCR in a bounded subprocess; verified extracted TXT and searchable index on Drive.
- Personal workspace export and explicit preview/confirmation restore of inert tasks/projects/facts/calendar only. Delivery jobs are excluded from restore.
- Reviewed natural text/voice phrases invoking existing handlers; /voice_help. Voice is archived before command execution. Group-send, deletion and restore confirmation cannot be executed through voice interpretation.

## User-confirmed live checks

Earlier Telegram checks confirmed photos, primary image, local image editing, voice archive/search, EPUB import and preference retrieval. Reminder save and subsequent Telegram delivery were confirmed. The workspace menu was confirmed working. Latest natural voice commands have automated coverage but still await the user's live voice check.

## Limits and unfinished integrations

- Free Render uptime and schedule precision are best effort.
- Google Calendar OAuth/synchronization is not implemented; current calendar is internal with ICS interchange.
- Authenticated website actions need a specific service adapter and authorization; current connections read public HTTPS content only.
- PDF/DOCX originals remain in Telegram; extracted text, not the original binary, is saved in Drive. This is disclosed to the user.
- OCR is limited to five sparse-text PDF pages per import, PDF to 80 pages, document upload to 8 MiB, extracted text to 300,000 characters.
- Improved recall uses lexical expansion and bounded paging, not paid embeddings or an exhaustive semantic index.
- Active-fact deletion does not erase historical messages or backup copies.
- Health checks do not validate every backup's contents or detect complete host outages from inside the stopped host.
- Group tools need the bot added with appropriate rights and live tests in the user's selected groups. No initial advertisement or unsolicited publication was sent by the development work.
- No YouTube integration or video-generation provider has been connected. YouTube animation planning is a separate new workstream; no channel/content has been published.

## Resume

Read ASSISTANT_WORKSPACE_uk.md, TELEGRAM_AUTOMATION_uk.md, VOICE_NOTES_uk.md, LIBRARY_HISTORY_uk.md and NEW_SKILLS_uk.md. Main entry is app.py. No Apps Script update is needed for PRs #19/#20. Preserve owner separation, explicit publication confirmation, existing deployment secrets and delivery ledgers.
