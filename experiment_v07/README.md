# v0.7: 8091 WebSocket media experiment

Latest, 2026-10-18 22:45 Asia/Shanghai: [correlated diagnostics and recovery](../docs/deploy-recovery-20261018.md) is deployed to 8091 only. Same-session/frame send and receive lengths/CRC can now be joined in the phone export. Admission stays bounded by four frames and bytes, while delayed feedback no longer repeatedly penalizes pacing within one feedback interval. Expired unsent H.264 is reset at an acknowledged boundary on the same WebSocket, rather than aborting the connection. Desktop synthetic and Pi loopback tests pass; real iPhone 4G short-packet origin and performance improvement are still awaiting correlated evidence. 8089/8090 and encoder configuration are unchanged.

Previous release, 2026-10-18 22:12 Asia/Shanghai: [integrity and byte/age admission](../docs/deploy-delivery-20261018.md) was deployed on 8091 only, with the prior schema-8 image archived. The independent 8091 browser rejects incomplete JPEG before decode and can continue after a rejected independent frame. This release introduced four in-flight frames plus byte/ACK-age bounds. Its [phone retest](../results/delivery-retest-20261018/README.md) found remaining short packets and excessive H.264 reconnects.

Update, 2026-10-18: [schema 8 measurement export](../docs/mobile-metrics-schema8.md) adds endpoint, implementation identity, per-session configuration, conditions and timing stages. After local testing, the user explicitly released the freeze for this change on 8091 only. It is deployed there; 8089/8090 and transport policy are unchanged. See the [deployment and rollback record](../docs/deploy-metrics8-20261018.md).

This is an isolated experiment on the existing 8091 video page. The camera is read only through the shared-memory adapter; 8089 and 8090 are preserved. The previous WebRTC/ICE code remains in `experiment_v06/` and is not the running 8091 implementation.

## Current route

- Default: 1280x720 H.264/fMP4 over WebSocket to Safari MediaSource. Existing v0.5 browser playback and ROI controls are reused; the v0.7 server owns transport scheduling.
- The sender samples the newest complete input frame when a paced send slot opens. At most four encoded frames await ordered browser append acknowledgements, subject also to a 54000-byte budget at 2400 kbps and a soft ACK-age admission limit. One oversized keyframe may travel alone; at most one prepared unsent packet is retained. After all transmitted fragments are acknowledged, an expired unsent H.264 reference chain is replaced with a new encoder/decoder generation on the existing socket. Long loss of ACK or a blocked write still terminates the connection. See the release record for limits and tradeoffs.
- `/api/status` reports numerical ACK and display feedback. It does not expose images, credentials, or peer addresses. `/jpeg-probe` is a separate full-frame JPEG negative-control protocol, not the default page.
- `/api/diagnostics` on the same port accepts allowlisted numeric fault reports and returns compact session/fault neighborhoods. The download button includes these as `server_diagnostics`; no query suffix or new viewing port is required.
- `compose.ws.yml` replaces only the experimental `viewer` service and publishes it on Pi loopback `127.0.0.1:8091`. The pre-existing direct Tailscale node serves this on the same fixed 8091 tailnet port; its 8089/8090 forwards are untouched. The older, relayed v0.6 VPN node is stopped and retained for rollback. No fourth video webpage port is created.

## Why this changed

The first v0.7 variant allowed only one frame in flight and waited for a browser append ACK before reading the next input frame. On iPhone 4G the user observed about 2679.7 ms frame age and 1.0 fps; service ACK times sometimes exceeded one second. One-at-a-time delivery therefore coupled the send rate to the remote feedback round trip. The eight-frame bound removes that artificial cap while retaining backpressure. A subsequent phone session on the independent VPN node still reported about 1150 ms frame age and 3.5 fps with a 1665 ms ACK. The old access node showed a direct phone path while the independent VPN node had relayed the phone via DERP, so the experiment now uses the old node's direct path. The user then reported 12-14 fps and commonly 180-250 ms frame age, but intermittent 300-1000 ms spikes; one Pi numeric sample reached about 2.7 s frame age and a 1.8 s ACK before recovery. This route is usable but not yet consistently low-latency.

The earlier full-frame 720p JPEG probe was also rejected as the primary route: its frame sizes exceeded the 2400 kbps budget at the requested cadence in synthetic testing. ROI/JPEG in the retained 8090 page is a different, working comparison and must not be conflated with this full-frame probe.

## Verification and limitations

`python -m unittest discover -s tests -p 'test_ws_*_v07.py' -v` passed seven focused tests. Synthetic Edge mobile and desktop viewports decoded without overflow; the Raspberry Pi shared-input smoke decoded 35/35 1280x720 fragments at about 12.9 fps on the same host. These results do not establish iPhone 4G latency. The direct-path phone result above is preliminary feedback, not a controlled P95 comparison with 8090.

All reported frame ages are clock-calibrated software estimates from publication to browser presentation. They are not optical exposure-to-display measurements. The process-A/B rectangles are user-selected task regions, not validated AI detections or industrial process measurements.
