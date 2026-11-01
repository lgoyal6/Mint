# Public source and data boundary

This source snapshot contains no production database, lead export, event attendee
list, or credentials. The production database stays in Neon and is configured
through the server-only `DATABASE_URL` environment variable. Publishing source
does not publish that database or its access credentials.

The private operational company prospect list and event-specific workbook with
lead verification notes are excluded. Enrichment fixtures use fictional people
and reserved example.com addresses. Public company names in classifier tests
are test inputs, not customer or prospect records.

Keep `.env`, database files, dumps, logs, input lists and generated exports out of
Git. Run the application with authentication enabled before connecting real data.
The public repository must start from this sanitized snapshot, not the old Git
history, because that history contains excluded operational records.
