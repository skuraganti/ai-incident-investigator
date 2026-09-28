# Faultline browser demonstration

Serve static `docs` files or use GitHub Pages. The full Python/Docker application and local startup instructions are in the [source repository](https://github.com/skuraganti/ai-incident-investigator).

The page inspects five saved local cases, six captured healthy/experiment requests per case, their corresponding request/log/span observations, filtered evidence facts and either a saved deterministic baseline or saved model attempt. Citation controls focus the exact referenced fact. Failed, timed-out or rejected model outputs do not produce a diagnosis or repair. Only saved validated results are shown; a pending allowlisted repair is human-review-only and nonexecutable. Export downloads the selected fixture case locally.

The study summary reports 2 of 7 validated conditions. Incident causes were explicitly injected; request samples are small and correlated. These observations establish neither production root cause nor repair efficacy. Source snapshot, timestamps and evidence digest are displayed. Frozen USGS source: [GeoJSON feed](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php).
