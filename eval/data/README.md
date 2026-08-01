# Eval data

```
eval/
  data-pipeline/              # collect → env → reference → curate (sibling of data/)
  data/
    langbridge-bench/         # curated internal specs + docker-images
    public/                   # verified / pro (original SWE data only)
```

Curate writes `eval/data-pipeline/curate/out/`, then **copies** into
`langbridge-bench/specs/`. Human drops live under `langbridge-bench/drop/`.
