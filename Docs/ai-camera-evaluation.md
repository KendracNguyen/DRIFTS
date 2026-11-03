# Raspberry Pi AI Camera (IMX500) for DRIFTS — evaluation

Researched 2 Nov 2026. Question: does moving from the Arducam OV5647 to
the Sony IMX500 AI Camera help, and can it produce the facial signals
DRIFTS needs?

## Verdict

**The compute argument is right and the benefit is bigger than assumed.
The blocker is not compute — it is that no face model exists for this
sensor, and you cannot download one.**

Two corrections to the premise:

* **Frame rate is not "similar to the Arducam."** On a Zero 2 W the CPU
  path cannot sustain landmarks at all. Nearest measured anchor: a Pi 3B+
  (same Cortex-A53, 1.4 GHz vs the Zero 2 W's 1.0 GHz) runs MobileNet-SSD
  300×300 under TFLite at **379.6 ms ≈ 2.6 fps** for *one* forward pass.
  A full MediaPipe FaceMesh DMS loop measures **12.88 fps on a Pi 4B**,
  and the Zero 2 W is 3–4× slower than that. So expect **2–4 fps on CPU**
  — below the rate at which blink duration and PERCLOS are measurable at
  all. The IMX500 takes this to the 30 fps sensor cap.
* **The AI Camera will not give you true gaze**, on any path. More below.

## What the sensor actually is

| | |
| --- | --- |
| On-sensor memory | **8,388,480 bytes** total — firmware + weights + working memory |
| Input tensor | ≤ 640×640 int8/uint8 (RPi brief); Sony's own page says 640×480 — **verify before designing to it** |
| Sensor modes | 4056×3040 @ 10 fps, 2028×1520 @ 30 fps → **30 fps hard cap** |
| Host load | <10% of a Pi 5 for parsed-output models |
| Physical | 25 × 24 × **11.9** mm, mounting holes match Camera Module 3 |
| Operating range | **0–50 °C** |

Measured inference, closest analogue to what DRIFTS needs: **TinyTracker**,
a MobileNetV3 gaze regressor at 112×112, runs **0.86 ms in-sensor**, +0.24 ms
to retrieve, ~19 ms end-to-end (camera read/ISP/transmit is the other 95%).
The accelerator is not the bottleneck for a model this size.

## The three structural constraints

**1. One network, and switching stops the stream.**
The .rpk format and Sony's converter support multiple networks with
ordinals, but the Raspberry Pi kernel driver hardcodes
`cci_write(regmap, CCI_REG8(0xD701), 0, NULL)` — *select network 0* — and
nothing in the driver, libcamera or picamera2 ever writes anything else.
`imx500_set_stream()` calls `__v4l2_ctrl_grab()` on the network-firmware
control, so it is read-only while streaming. Changing models means tearing
down and recreating the `Picamera2` object.

**This kills any two-stage detect-then-landmark pipeline**, which is how
MediaPipe (BlazeFace → FaceMesh) and most face-landmark stacks are built.
Sony's own answer to "run two models" is to use two cameras.

**2. The supported-operator set is narrow, and it excludes PReLU.**
The authoritative list is `uni/pytorch/onnx_parser/onnx_op_to_factory.py`
inside `uni-pytorch` 3.19.0 — 55 ONNX ops. Not supported, among others:
`PRelu`, `Elu`, `Mish`, `Erf`, `Log`, `Where`, `Tile`, `GridSample`,
`Einsum`, `LSTM/GRU/RNN`, ONNX's own `NonMaxSuppression`.

**3. `.tflite` is not an accepted input format** — only Keras or ONNX.

MediaPipe FaceMesh therefore fails three independent ways: it ships only
as .tflite, it is built on PReLU, and it is the second stage of a
two-stage pipeline. It is not "slow on this hardware," it is structurally
unsupported.

## What models exist today — and what they give you

**There is no face model in any IMX500 zoo.** Checked:
`raspberrypi/imx500-models` (25 .rpk), Sony's `aitrios-rpi-model-zoo`
(22), the `modlib` zoo (24 classes), Arducam's zoo. The closest thing is
**PoseNet / HigherHRNet**, 17 COCO body keypoints. Sony's own `blur-face`
example calls `Posenet()` because there is no face model to call.

Those 17 points give exactly **nose, left eye, right eye, left ear, right
ear** — one point per eye, nothing for the mouth. Against your list:

| Signal | With stock PoseNet | Why |
| --- | --- | --- |
| Head **roll** | ✅ good | angle of the inter-eye line |
| Head **yaw** | ✅ good | ear visibility asymmetry + eye/nose offsets |
| Head **pitch** | ⚠️ weak | 5-point PnP has a known pitch/translation ambiguity |
| Off-road gaze | ⚠️ head-pose proxy only | no iris — cannot catch "eyes down, head forward" |
| **EAR** / eye openness | ❌ impossible | needs ~6 points per eye; you have 1 |
| **Blinking** | ❌ impossible | same |
| **Mouth aperture** | ❌ impossible | no mouth keypoints at all |
| **Yawn** | ❌ impossible | same |

Also note HigherHRNet's published mAP is **0.188** — poor even for body
pose. Sony's `sleep-detection` example is not eye-state detection: it
declares `SLEEP` when ≥5 of 17 body keypoints stop moving. Their
`driver_detect` example detects a *phone*, not driver state.

**This is exactly what you found.** Bounding boxes and coarse keypoints
exist; dense landmarks do not. The reason is structural, not a gap in your
searching.

## The path that does exist: a custom single-stage landmark net

**PFLD is the right candidate, and I verified it converts on paper.**
`polarisZhao/PFLD-pytorch`'s `models/pfld.py` uses **only** `Conv2d`
(incl. depthwise), `BatchNorm2d`, `ReLU`, `AvgPool2d`, `MaxPool2d`,
`Linear`, a residual add, `cat` and `view`. Every one of those is in the
supported ONNX op list. **No PReLU anywhere.** Input 3×112×112, output
196 values = 98 WFLW landmarks.

Those 98 points include 8 per eye and 20 around the mouth — enough for
EAR, mouth aperture, yawn and blink, and enough for a proper solvePnP head
pose. The `AuxiliaryNet` head is training-only and gets dropped at export,
so the deployed graph is simpler still. At 112×112 it is in the same size
class as TinyTracker's 0.86 ms net, and far inside the 8 MB budget.

**Solving the single-stage problem.** PFLD expects a face crop and you
cannot run a detector first. Two ways round it, both viable here because
the camera is rigidly mounted and the driver's head sits in a predictable
zone:

* **Fixed ROI** — `IMX500.set_inference_roi_abs((x, y, w, h))` sets the
  crop fed to the network, in full-sensor coordinates. Pick a generous box
  covering the head zone; train with scale/translate augmentation.
* **Frame-delayed tracked ROI** — run PFLD, compute the landmark bbox, set
  the ROI for the next frame. One frame of lag (33 ms) against a head that
  moves slowly. Widen the ROI to re-acquire when confidence drops.

**Better still: one multi-task network.** `MAX_NUM_TENSORS = 16`, so a
single network can emit several output tensors. Landmarks + a direct head-
pose regression head + a direct gaze head in one graph sidesteps the
single-network limit entirely and is the architecture the hardware wants.
TinyTracker is precedent for the gaze head specifically — it dropped
iTracker's multi-input eye-crop design for exactly this reason.

## Honest success estimates

| Step | Estimate | Basis |
| --- | --- | --- |
| Stock PoseNet running on Zero 2 W + AI Camera | **~90%** | Sony has a published Zero 2 W walkthrough |
| Head yaw + roll from it | **~90%** | geometry is robust |
| Head pitch | **~60%** | weak axis with 5 points |
| PFLD converts to .rpk and produces output | **~70%** | all ops supported, static graph, small input — risk is toolchain versions |
| Mouth aperture / yawn accurate enough | **~75%** | large, high-contrast motion |
| **EAR / blink accurate enough to avoid false alarms** | **~50%** | this is the real risk — see below |
| True gaze (iris direction) | **~0% as landmarks** | no iris points in WFLW; needs its own head |

The EAR number is the one to take seriously. At a 112×112 head crop an eye
spans roughly 20×10 px, and EAR needs eyelid points good to 1–2 px. PFLD's
WFLW NME is ~6–7% inter-ocular before int8 quantisation degrades it.
Expect to need heavy temporal filtering, and budget for the possibility
that blink *duration* is not recoverable even when blink *detection* is.

## Known risks before any hardware spend

* **picamera2 issue #1404** — `capture_metadata()` hangs permanently on a
  Pi Zero 2 W + IMX500 after ~1,561 frames, reproduced 4/4 by the
  reporter across units and cables, kernel 6.12.47 / picamera2 0.3.36 /
  libcamera 0.7.1. Maintainer davidplowman **could not reproduce it** on a
  fresh OS, pointed out the reporter's own frame-count and duration
  figures don't agree, and suspects power delivery. So: an open,
  unconfirmed report, not a proven blocker — but a permanent hang in a
  fatigue monitor is safety-relevant, so **test for it in the first hour
  of owning the hardware.**
* **First model load will be slow on a Zero 2 W.** The RP2040 bridge's
  fast parallel-GPIO path is enabled only when the GPIO controller is
  `raspberrypi,rp1-gpio`, which exists only on the Pi 5. On a Zero 2 W
  every cache-miss block goes over the camera-connector I²C bus. Cached
  loads are still seconds; `apt install imx500-all` alone takes 10–15 min.
* **0–50 °C operating range** in a Houston car cabin. I did not find a
  comparable figure for the OV5647, so this may not be a regression — but
  a parked car exceeds 50 °C routinely.
* **No published power figure** for the module. In-sensor inference is
  ~72 mW; the module also carries an RP2040 and flash. Measure it rather
  than budgeting from a datasheet, because there isn't one.
* **Conversion toolchain is version-brittle**: PyTorch 2.3–2.6, Python
  3.10–3.12, ONNX opset 15–20, JVM 17, 4 GB RAM on the conversion host.
  A documented trap: installing `imx500-converter` after MCT downgrades
  `onnx`/`networkx`/`mct-quantizers` and breaks quantisation — **quantise
  first, then install the converter.**

## Enclosure impact

The AI Camera's PCB outline and mounting holes match Camera Module 3, so
it drops onto the **existing 21.0 × 12.5 M2 pattern** in the rev 7/rev 8
front face — no change to the hole pattern. Two things do change:

* **Depth 11.9 mm**, against the 1.6 mm board the model currently
  assumes. In rev 8 the camera sits at Z 40…64 with nothing behind it, so
  it fits, but the envelope in `verify.py` needs updating.
* **Lens position**. The Ø10 aperture is placed from the UC-346 DWG,
  where the sensor sits 5.1 mm from the board's lower edge. Camera Module
  3's lens centre is in a different place — **re-measure before cutting a
  new front panel.**

## Recommendation

Switch, but stage it so the risk is bought down before the model work:

1. **Buy one AI Camera and run Sony's Zero 2 W walkthrough.** Confirm the
   pairing works on your hardware and leave it streaming for an hour to
   test for #1404. Cheap, and it gates everything else.
2. **Ship head pose on stock PoseNet.** Yaw, roll and a head-pose gaze
   proxy, working, with the CPU free. That is three of your eight signals
   and it is a week of work, not a month.
3. **Then attempt the PFLD conversion** for EAR, mouth aperture, yawn and
   blink, validating against `--memory-report` and `get_kpi_info()` early.
   Treat this as research with a real chance of failure, not a task.
4. **Keep the OV5647 as the fallback** for step 3. If PFLD doesn't
   converge, the honest alternative is dropping blink-level fatigue
   signals rather than pretending a 3 fps CPU pipeline measures them.

Do not plan on true iris gaze from this sensor without a dedicated
regression head, and do not plan on two networks.

## Repos worth building on

| Repo | Use it for |
| --- | --- |
| [SonySemiconductorSolutions/aitrios-rpi-training-samples](https://github.com/SonySemiconductorSolutions/aitrios-rpi-training-samples) | **The highest-leverage one.** `docs/Posenet_NewDataset.md` + `samples/posenet_arrow.ini` are the only vendor-documented path to a *custom keypoint* .rpk — a 7-keypoint model trained from COCO-format JSON with MCT quantisation built in. Swap the arrow vertices for eye/mouth contour points. Caveat: trains from scratch, and Sony publishes no accuracy figures. |
| [polarisZhao/PFLD-pytorch](https://github.com/polarisZhao/PFLD-pytorch) | The landmark architecture. Verified ReLU-only, 112×112, 98 points, has `pytorch2onnx.py`. 876 stars but 46 open issues — semi-abandoned, so expect to maintain it yourself. |
| [MustafaNazeer/SafeEyes](https://github.com/MustafaNazeer/SafeEyes) | **Take the decision layer, not the perception layer.** Trained temporal fusion over a 150-frame window, leave-one-subject-out validation, committed metrics, 7 ADRs. Turning noisy per-frame EAR into a PERCLOS decision without constant false alarms is the part nobody else has done. Its 12.88 fps Pi 4B figure is also your honest baseline for the MediaPipe route. |
| [raspberrypi/picamera2-examples](https://github.com/raspberrypi/picamera2-examples) `examples/imx500/` | Runtime plumbing — the pose demo is the canonical example of parsing a keypoint tensor out of IMX500 metadata and rescaling coordinates. |
| [aitrios-rpi-application-module-library](https://github.com/SonySemiconductorSolutions/aitrios-rpi-application-module-library) (modlib) | Cleaner `AiCamera`/`deploy`/`Annotator` API one layer above picamera2. Faster iteration; drop to raw picamera2 for a custom output tensor. |
| [PINTO0309/PINTO_model_zoo](https://github.com/PINTO0309/PINTO_model_zoo) | Source models, very active: `032_FaceMesh`, `300_6DRepNet`, `398_L2CS-Net`, `436_Peppa_Pig_Face_Landmark`, `448_YOLOX-Eye-Nose-Mouth-Ear`. Nothing IMX500-specific — a library, not a deployment path. |
| [pbonazzi/picosam3](https://github.com/pbonazzi/picosam3) | The cleanest published proof a non-stock architecture reaches the IMX500 (~1.2 MB distilled SAM, 96×96 ROI). Read it for the ROI trick. |
| [TinyTracker (arXiv 2307.07813)](https://arxiv.org/pdf/2307.07813) | Closest precedent — gaze regression on IMX500, 112×112, 0.86 ms. Read why they dropped the multi-input design. |
| [Sony's Zero 2 W + AI Camera walkthrough](https://www.hackster.io/sony-aitrios/tiny-but-mighty-raspberry-pi-ai-camera-on-pi-zero-2-w-68c15b) | The only confirmation this exact pairing works. Requires **Pi OS Lite 64-bit** and `pip` pinned to 24.0. Publishes no fps. |

## Sources

Raspberry Pi [AI Camera docs](https://www.raspberrypi.com/documentation/accessories/ai-camera.html) and
[product brief](https://pip-assets.raspberrypi.com/categories/1044-raspberry-pi-ai-camera/documents/RP-008147-DS-1-ai-camera-product-brief.pdf);
[picamera2 #1404](https://github.com/raspberrypi/picamera2/issues/1404);
[raspberrypi/linux `imx500.c`](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/drivers/media/i2c/imx500.c) and
[`spi-rp2040-gpio-bridge.c`](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/drivers/spi/spi-rp2040-gpio-bridge.c);
[raspberrypi/imx500-models](https://github.com/raspberrypi/imx500-models);
[aitrios-edge-mdt](https://github.com/SonySemiconductorSolutions/aitrios-edge-mdt);
[Ultralytics IMX500 integration](https://docs.ultralytics.com/integrations/sony-imx500);
PyPI packages `imx500-converter` 3.19.2 / `uni-pytorch` 3.19.0 inspected directly for the operator list;
[Tom's Hardware review](https://www.tomshardware.com/raspberry-pi/raspberry-pi-ai-camera-review-ai-for-the-masses);
[aallan/benchmarking-ml-on-the-edge](https://github.com/aallan/benchmarking-ml-on-the-edge) for the Pi 3B+ anchor.

---

## Review notes (3 Nov 2026)

Second review before acting on this evaluation. The staged plan is adopted:
stage 2 (head pose on stock PoseNet/HigherHRNet) is built, see
[`ai-camera-stage2.md`](ai-camera-stage2.md).

**Spot-checked claims**

| Claim | Result |
| --- | --- |
| PFLD inference graph is ReLU-only, 112×112 in, 196 out | ✅ Confirmed in [`models/pfld.py`](https://github.com/polarisZhao/PFLD-pytorch/blob/master/models/pfld.py): Conv2d (incl. depthwise), BatchNorm2d, ReLU, AvgPool2d, Linear, add, cat, view. No PReLU |
| picamera2 #1404 (Zero 2 W + IMX500 hang) | ✅ As described: open, maintainer could not reproduce, suspects power delivery |
| Sony's Zero 2 W walkthrough exists | ✅ [Hackster, Sony AITRIOS](https://www.hackster.io/sony-aitrios/tiny-but-mighty-raspberry-pi-ai-camera-on-pi-zero-2-w-68c15b); Tom's Hardware reports 30 fps on a Zero 2 W demo |
| SafeEyes 12.88 fps on Pi 4B | ✅ [edge-benchmark.md](https://github.com/MustafaNazeer/SafeEyes/blob/main/docs/perf/edge-benchmark.md): drowsiness-only, headless, driver in frame. Full pipeline with HUD: 11.1 fps |
| SafeEyes "leave-one-subject-out" validation | ⚠️ Not found: the repo states a "subject-independent split" (related, weaker). MIT licence |
| picamera2 IMX500 API used by stage 2 | ✅ `IMX500(...)`, `camera_num`, `get_outputs`, `get_kpi_info`, `set_auto_aspect_ratio`, `set_inference_roi_abs`, `postprocess_higherhrnet` checked in the picamera2 source; usage mirrors `picamera2-examples/examples/imx500/imx500_pose_estimation_higherhrnet_demo.py` |

**Gap: night driving.** The evaluation does not mention that the AI Camera
has an **integrated IR-cut filter and no NoIR variant** (its own cited
product brief: "Infrared sensitive: No"). In a dark cabin it needs visible
light. Decision (3 Nov): night operation is *preferred, not required*; the
lighting options are in `ai-camera-stage2.md`. The fallback camera should be
the **OV5647 NoIR + IR LEDs**, and PFLD (trained on visible-light WFLW)
would need IR examples to work at night.

**Smaller corrections**
- "Use two cameras" (Sony's answer to two networks) is not available here:
  the Zero 2 W has one camera connector.
- Hardware choice is now fixed: **Pi Zero 2 W stays**. That makes the AI
  Camera route the right one (the CPU path would be ~2–4 fps).
- The enclosure section refers to rev 7/rev 8 front faces and a `verify.py`
  that are not in this repository; re-measure the lens position as stated.
