# Minimal Streamlit Runtime — #014-R1

The application uses only verified canonical Evidence selected from the V1 Evidence table. It serially prepares indicators and the frozen seven-field Prompt, displays COLLECTING/NORMALIZING/READY_FOR_REVIEW/ARCHIVED, DATA_STATUS, Evidence count, Prompt and per-item human decisions. Missing realtime, financial/information inputs remain explicitly PARTIAL. Human confirmation never upgrades source quality.

Install `requirements-dashboard.txt` in a local virtual environment. Configure explicit runtime selectors without committing them:

```sh
export V1_RUNTIME_FACTORY=dashboard.factory:create_runtime
export V1_RUNTIME_DB_SELECTOR=/absolute/local/runtime.sqlite
export V1_RUNTIME_ARCHIVE_ROOT=/absolute/local/archive
# Optional configured ModelExecutor factory, no built-in/default/live model:
# export V1_RUNTIME_EXECUTOR_FACTORY=your_local_executor:create_executor
python -m streamlit run dashboard/app.py
```

An existing Schema B DB and canonical V1.1 Evidence are required; the launcher never migrates or creates a database. No raw response or Provider payload is displayed. Without an executor, the user can inspect indicators/Prompt while report generation is disabled. Factory selection is trusted local operator configuration, not a user-supplied web input. No deployment or network-facing authentication is added.

The workflow is explicit: select Evidence → prepare → inspect indicators/Prompt → generate an unconfirmed report using the configured executor → confirm/reject each required item → prepare immutable final snapshot → inspect snapshot → final human confirm and archive. Existing ConfirmationFlow gates, append-only confirmation events, final evidence bindings and repository transactions perform the writes; only V1.1 new tables are touched. Preparation states are session history before a model run exists; READY_FOR_REVIEW/ARCHIVED are persisted task statuses afterward.

Failed progress writes leave in-memory decisions unchanged. Archive retries reuse the same final IDs and actor; append-only bindings created before a failed DB commit are inert until the final DB record exists. Browser disconnect/process restart does not reconstruct sealed review objects from read-model DTOs; starting an interrupted review again requires a fresh run. Existing historical confirmation read models remain available. Rejected revisions are retained; this minimal UI does not add revision editing.

UI tests use the real [Streamlit AppTest API](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest), with explicit button clicks and DB assertions. They are skipped when Streamlit is absent locally, and required by the new CI workflow. A skipped UI test is not E6 PASS. Dev-box tests use temporary synthetic data and a labeled test executor; they do not certify qi's Windows E1/E2/E7 or a real model backend.
