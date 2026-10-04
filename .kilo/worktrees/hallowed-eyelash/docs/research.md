# Research Notes — TikTok upload fidelity

## Official baseline

TikTok's 2026 Media Transfer Guide lists MP4 as recommended, H.264 as recommended, 23–60 FPS, 360–4096 px for width/height and 4 GB maximum file size.

Source: https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide

## Public reverse-engineered approaches

### 1. Timing metadata patch

`ut0ku/120fps-method` says it modifies `mvhd` and `mdhd` time scales and durations without changing media packets, with a divisor of 2 for 60 FPS and 4 for 120 FPS.

Source: https://github.com/ut0ku/120fps-method

Repository license: GPL-3.0.

### 2. Sample-table inflation

`BastienGimbert/tiktok-quality` and `irgifebry/NoBlur` describe another class of technique: increase MP4 sample-table density with tiny filler samples while retaining the original encoded picture data.

Sources:
- https://github.com/BastienGimbert/tiktok-quality
- https://github.com/irgifebry/NoBlur

### 3. H.265 + patch

`MisticGG/fps-method` re-encodes locally to H.265 at 60fps, then patches an MP4 container field. Because this includes a local re-encode, it is not the default direction for this repository.

Source: https://github.com/MisticGG/fps-method

### 4. Structural mismatch experiment

`buwryme/tiktok-lossless-upload` describes an experimental approach involving extra track/sample-table structure and custom metadata, intended to cause a server-side passthrough behavior.

Source: https://github.com/buwryme/tiktok-lossless-upload

## Why the repository defaults to sample-table inflation

The project goal is strict:

- no quality loss before TikTok
- no frame interpolation
- no AI sharpening
- no local re-encode
- preserve HDR/DV payloads

That makes sample-table inflation the more relevant first experiment when the goal is to keep the local media stream at its original FPS. The timing-only method is retained as a separate experiment because it is extremely small and fully byte-preserving.

## What this research does NOT prove

None of these repositories is an official statement from TikTok about how its production transcoder works. Backend behavior can differ by account, geography, client, upload path and time. A successful test on one video does not establish a universal guarantee.
