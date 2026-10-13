# Safari waiting observations — venture lifecycle

## Review on 2026-10-13

**Venture lifecycle: elapsed feedback observed; watchdog/retry and navigation
reported successful by the user.** This is a limited manual device check, not a
full physical-device matrix sign-off.

A user-submitted synthetic-device report was reviewed. It records an iPhone 12
and an entered iOS version of **27.2**. Although the report's physical-Safari
checkbox was false, the user subsequently confirmed that the check ran in Safari
on a physical iPhone and that **27.2** is the version shown in
Settings → General → About. Device and OS metadata are therefore user-confirmed,
not inferred from the browser's user-agent version.

## Observed elapsed-before-text behavior

The report contains five venture elapsed-before-text starts. The first four
were reset before backgrounding. Only the final run has a hidden → visible
transition:

- Hidden at 2.161 seconds; visible again at 61.276 seconds: about 59.115 seconds
  in the background.
- On return the UI read “Considering your idea… 61s. Still working”.
- The two-second settled sample showed 63.294 seconds on both clocks and 62s in
  the feedback, within the documented three-second painting tolerance.
- Across the recorded samples wall and performance elapsed time differed by at
  most one millisecond.
- No recorded JavaScript errors or unexpected requests.

This is evidence that elapsed feedback included the background interval in
that run. Visibility events do not prove that iOS suspended JavaScript.

## Manual watchdog/retry and navigation confirmation

After confirming the physical device and iOS version, the user was given these
two venture cases and subsequently reported **“Worked”**:

- Watchdog/retry: start a stalled synthetic request, switch apps for 4 minutes
  15 seconds, return, record a snapshot, then use **Retry and complete**.
- Navigation after partial text: start the case, switch apps for 45–60 seconds,
  return, record a snapshot, navigate to About, switch apps again for 4 minutes
  15 seconds, and check that About remains open.

These outcomes are user-reported manual confirmation. A second observation JSON
was not supplied, so exact abort timestamps, timer counts, retry request counts
and interrupted/completed timing entries for these two cases were not
independently reviewed. Do not present them as machine-recorded device results.

## Evidence limitations and extended coverage

The report was exported while the final request was still waiting. Its abort
flag was false, with one feedback interval and one watchdog deadline still
active. That is expected for a live wait, not evidence of failed cleanup.

The initial uploaded report does not record completion, watchdog expiry, retry
completion, navigation, or interrupted timing entries. The later manual
confirmation does not change the contents of that original export.

No physical path or refinement run was supplied. The venture navigation
before-text variant and separate after-partial successful completion were not
individually reported. The extended matrix in
[the device procedure](ios-waiting-device-check.md) remains useful follow-up
coverage; automated coverage is not a substitute for those device runs.

No conclusion about a published host, upstream connection, or provider timeout
can be drawn. The fixture supplied all responses in memory.

## Handling and restoration

Raw uploaded observation JSON is excluded from Git and deployment packaging.
Only this reviewed summary is retained in documentation. The normal development
workflow was restored after the first review. The user then authorized reopening
the isolated fixture for the remaining checks. After the user's manual success
confirmation, the normal Python application workflow was restored again and
started successfully. No published-app configuration was changed.
