# 8091 WebSocket/H.264 mobile experiment, 2026-10-18

## Controlled change

8091 replaced the unsuccessful WebRTC/ICE phone path with WebSocket/H.264/fMP4. The working 8089 and 8090 entries, Tailscale identities, camera owner and 1280x720 input were not changed. This is the same third webpage port, not a fourth entry.

## Observed phone failure before correction

The user's iPhone Safari screenshot on 4G + Tailscale showed an actual image but marked it stale: 2679.7 ms current frame age, 1127.7 ms presentation-delay estimate, 1.0 displayed fps, 23.0 ms playback buffer, 0.191 Mbps effective payload, two reconnects, and 461.0 ms clock RTT. The target controls remained 2400 kbps and 20 fps. The Pi status from the same version recorded ACK times around 0.8-2 seconds and a freshness timeout. Its one-frame-in-flight stop-and-wait protocol is an identified contributor to the low send rate. The displayed 23 ms buffer does not support blaming a large MSE playback queue. Tailscale reported an active DERP relay for the phone at the time; route contribution remains unquantified and is not a sole-cause claim.

## Revised experiment

The server now permits up to eight outstanding frames, requires ordered ACKs, paces by the user byte budget, samples latest input only at a send slot, and reconnects if the oldest pending frame is unacknowledged for three seconds. Focused Python tests: 7/7 pass. Synthetic Edge at 390x844 and 1440x1000: about 131/132 presented frames, no page errors, reconnects, or horizontal overflow; synthetic frame-age P50 about 124/123 ms and P95 about 187/184 ms. The Pi same-host shared-input decoder accepted 35/35 fragments at 1280x720 and about 12.9 fps. These tests are not mobile-network measurements.

Both working pages returned HTTP 200 after deployment, as did 8091. On the independent 8091 VPN node, one revised phone session still reported about 1150 ms frame age, 3.5 fps, and 1665 ms ACK. The Pi's access-node status showed the phone was direct to the old 8090 node, while the independent 8091 node had previously reported a DERP relay. The 8091 viewer was therefore moved to Pi loopback and routed through the existing direct access node on the same port; the independent VPN node was stopped with restart disabled, not deleted. The direct access node's Serve status listed only 8089, 8090 and 8091, and the 8091 viewer was bound to `127.0.0.1:8091` on the host.

On the revised direct path, the user reported 12-14 fps and usually 180-250 ms current frame age, with occasional 300-1000 ms spikes. A Pi numeric-only status sample caught a worse short outage: ACK about 1786 ms and current frame age about 2681 ms, followed within seconds by about 100-200 ms. That sample does not establish its frequency. At another point status showed a direct iOS peer, about 68 ms ACK and about 178 ms frame age. Pi throttling flag was `0x0`, five-GHz Wi-Fi signal about -40 dBm, and driver counter 16 failed TX packets in about 60k at the observed time. These checks do not rule out transient radio or 4G congestion.

The direct-path revision still needs a controlled iPhone 4G comparison before any superiority claim. Alternate `100.80.123.16:8090` and `100.80.123.16:8091` on the same 4G/Tailscale connection, with other video pages closed, and record software frame age P50/P95, displayed fps, payload, reconnects, and whether Tailscale is direct or relayed. Preserve poor runs rather than choosing a favorable screenshot.

## Measurement caveats

The screenshot is a point sample, not a latency distribution. Clock RTT under an asymmetric path affects the estimated frame age; no optical glass-to-glass measurement was made. No real camera frame or personal image is stored in this results directory. ROI freshness is not verified ROI clarity, and no AI detector or industrial true-value task is claimed.
