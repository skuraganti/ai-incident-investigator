# Release status

**Local release gates: verified on 2026-10-16.** Source, native tests, Docker workflow/persistence, Pages browser/mobile/error states and publication privacy review passed. Actual publication is recorded by GitHub commit/deployment metadata after this prepared initial commit. See [VERIFICATION.md](VERIFICATION.md) for exact checks and limits.

Local: `sh stack.sh up` → http://127.0.0.1:8286. [Pages demo](https://skuraganti.github.io/ai-incident-investigator/) · [Source](https://github.com/skuraganti/ai-incident-investigator). Pages uses static browser interactions; full Python services run locally. Runtime databases, caches, model-control files and credentials stay excluded. Fault causes are deliberately injected; observed errors, HTTP results and durations are actual local measurements. The frozen model study validated2/7conditions; two timed out, two returned malformed JSON, and one failed before inference. A separate browser generation was also rejected. The injection answer had weak alternatives despite valid quotes. No production-RCA or repair-efficacy claim is made, and there is no repair executor.

Next maintenance action: use the documented setup, verify original-source checksums before updating fixtures, rerun affected tests and retain failed/uncertain evaluation outcomes. No hosted inference or unattended service is supplied by Pages.
