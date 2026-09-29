// Coaster, mirrored from the KNOMI. Same face, moods, quirks, feeling and decorations as the
// firmware (knomi_coaster.cpp). The KNOMI sends its state when it changes, and while this panel
// is open also where its head and pupils are, a few times a second; the rest (blinks, eye darts,
// quirk animations, snow) runs here, so it's close to the device but not frame-exact.
$(function () {
    // open, size, cheek, orbit, w, curve, omega, gape, zig, wave, tilt (same table as the firmware)
    var M = {"calm":[0.5,1.0,0.0,0.0,14.0,0.0,1.0,0.0,0.0,0.0,0.0],"riding":[0.56,1.0,0.0,0.0,14.0,0.3,0.7,0.0,0.0,0.0,0.0],"excited":[0.85,1.05,0.0,0.0,12.0,0.7,0.0,0.9,0.0,0.0,0.0],"screaming":[1.0,1.3,0.0,0.0,8.0,0.0,0.0,1.7,0.0,0.0,0.0],"startled":[1.0,1.2,0.0,0.0,6.0,0.0,0.0,1.1,0.0,0.0,0.0],"elevator":[0.75,1.05,0.0,0.0,7.0,0.0,0.0,0.6,0.0,0.0,0.0],"sleepy":[0.02,1.0,0.0,0.0,11.0,0.0,1.0,0.0,0.0,0.0,0.0],"bored":[0.32,1.0,0.0,0.0,9.0,0.0,0.0,0.0,0.0,0.0,0.0],"shivering":[0.42,1.0,0.0,0.0,14.0,0.0,0.0,0.0,1.0,0.0,0.0],"dizzy":[0.5,1.0,0.0,1.0,14.0,0.0,0.0,0.0,0.0,1.0,0.0],"giggle":[1.0,1.05,1.0,0.0,12.0,0.8,0.0,0.8,0.0,0.0,0.0],"celebrate":[1.0,1.1,1.0,0.0,15.0,0.9,0.0,1.0,0.0,0.0,0.0],"ready":[1.0,1.1,0.0,0.0,13.0,0.8,0.0,0.5,0.0,0.0,0.0],"sad":[0.35,1.0,0.0,0.0,12.0,-0.9,0.0,0.0,0.0,0.0,-0.7],"shocked":[1.0,0.75,0.0,0.0,10.0,0.0,0.0,0.35,0.0,0.8,-0.4],"lonely":[0.45,1.0,0.0,0.0,9.0,-0.6,0.0,0.0,0.0,0.0,-0.5],"confused":[0.55,1.0,0.0,0.0,10.0,0.0,0.0,0.0,0.0,0.5,0.3],"heating up":[0.42,0.95,0.0,0.0,13.0,0.0,0.0,0.0,0.8,0.0,1.0],"cooling off":[0.3,1.0,0.0,0.0,12.0,0.5,0.4,0.0,0.0,0.0,0.0],"focused":[0.28,0.9,0.0,0.0,7.0,0.0,0.0,0.0,0.0,0.0,0.0],"almost there":[0.8,1.05,0.0,0.0,12.0,0.5,0.0,0.2,0.0,0.0,0.0],"hungry":[0.7,1.0,0.0,0.0,8.0,0.0,0.0,0.9,0.0,0.0,0.0],"windy":[0.3,1.0,0.0,0.0,10.0,0.0,0.0,0.0,0.0,0.6,0.0],"hanging on":[0.9,0.9,0.0,0.0,10.0,0.0,0.0,0.0,0.6,0.0,-0.3],"bracing":[0.08,1.0,0.0,0.0,10.0,0.0,0.0,0.0,0.5,0.0,-0.3],"leveling":[0.5,1.0,0.0,0.0,12.0,0.0,0.0,0.0,0.0,0.0,0.0],"scrubbing":[0.8,1.0,0.6,0.0,12.0,0.5,0.0,0.0,0.0,0.6,0.0],"whee":[1.0,1.15,0.0,0.0,13.0,0.8,0.0,1.3,0.0,0.0,0.0],"mad":[0.36,0.95,0.0,0.0,12.0,-0.35,0.0,0.0,0.3,0.0,1.0]};
    var KEYS = ["open", "size", "cheek", "orbit", "w", "curve", "omega", "gape", "zig", "wave", "tilt"];
    var QDUR = {glance: 1.6, "double blink": 0.5, "slow blink": 1.3, wink: 0.8, yawn: 2.4, hum: 3.6, sneeze: 1.7,
        "look up": 1.9, stretch: 1.8, "eye roll": 1.3, nod: 0.7, cheer: 1.3, sigh: 1.9, huff: 0.9};
    function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
    function lerp(a, b, t) { return a + (b - a) * t; }
    function rnd(a, b) { return a + Math.random() * (b - a); }
    function bump(t, d, e) { return clamp(Math.min(t / e, (d - t) / e), 0, 1); }

    function CoasterViewModel(parameters) {
        var self = this;
        self.moodLabel = ko.observable("");
        self.reportLine = ko.observable("");
        self.seen = ko.observable(false);

        var st = {mood: "calm", h: 0.3, pr: 0, deco: "", lights: "classic", anim: "twinkle", shades: 0, c: "#C02F30", act: 0, heat: 0,
                  hx: 0, hy: 0, hs: 0, px: 0, py: 0, look: 0, live: false, hat: 0};
        var E = {}, head = {x: 0, y: 0, s: 0}, pup = {x: 0, y: 0}, look = 0, wander = 0, wanderT = 0;
        var blinkT = 3, blinkC = 0, now = 0, last = 0, moodT = 0;
        var Q = {name: "", t: 0, side: 1, fired: {}}, QF = {}, notes = [], sacc = {x: 0, y: 0, tx: 0, ty: 0, t: 1};
        var confetti = [], parts = [], fw = [], fwT = 1, spawnT = 0, shades = 0, decoKind = "";
        KEYS.forEach(function (k, i) { E[k] = M.calm[i]; });

        self.apply = function (c) {
            if (!c || !c.mood) return;
            self.seen(true);
            if (c.mood !== st.mood) { moodT = 0; if (c.mood === "celebrate") spawnConfetti(); }
            if (c.q && c.q !== Q.name && QDUR[c.q]) { Q.name = c.q; Q.t = 0; Q.fired = {}; Q.side = c.qs || 1; }
            ["mood", "h", "pr", "deco", "lights", "anim", "shades", "c", "act", "heat", "hat"].forEach(function (k) { if (c[k] !== undefined) st[k] = c[k]; });
            if (c.hx !== undefined) { st.hx = c.hx; st.hy = c.hy; st.hs = c.hs; st.px = c.px; st.py = c.py; st.look = c.look; st.live = true; st.liveAt = Date.now(); }
            self.moodLabel((c.mood === "whee" ? "Whee!" : "Coaster is " + c.mood) + (c.feel ? " · feeling " + c.feel : ""));
            var r = c.report;
            if (r) {
                var h = Math.floor(r.secs / 3600), m = Math.floor((r.secs % 3600) / 60);
                self.reportLine("Last print: " + (r.done ? "done" : "stopped at " + r.progress + "%") + ", " + (h ? h + "h " : "") + m + "m · " +
                    r.screams + " screams · peak " + Number(r.peak).toFixed(1) + " g · dizzy " + r.dizzies + "x");
            }
        };
        self.onStartup = function () {
            OctoPrint.simpleApiGet("knomi").done(function (r) { self.apply(r && r.coaster); });
            requestAnimationFrame(frame);
            // tell the plugin someone is watching, so the KNOMI sends head motion
            setInterval(function () {
                var cv = document.getElementById("knomi_coaster_canvas");
                if (cv && cv.offsetParent && !document.hidden) OctoPrint.simpleApiCommand("knomi", "coaster_watch", {});
            }, 5000);
        };
        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin === "knomi" && data && data.coaster) self.apply(data.coaster);
        };

        function spawnConfetti() {
            for (var i = 0; i < 40; i++) confetti.push({x: 120 + rnd(-20, 20), y: 120, vx: rnd(-160, 160), vy: rnd(-260, -80), r: rnd(0, 6.28), vr: rnd(-8, 8), life: rnd(1.8, 3.2)});
        }

        /* ---- quirks: the KNOMI says which one, the animation plays here ---- */
        function once(n) { if (Q.fired[n]) return false; Q.fired[n] = 1; return true; }
        function stepQuirk(dt) {
            QF = {look: 0, lookY: 0, openL: 1, openR: 1, gape: 0, curve: 0, w: 0, cheek: 0, dx: 0, dy: 0, sq: 0};
            sacc.t -= dt;
            if (sacc.t <= 0) { sacc.t = rnd(0.4, 2.2); sacc.tx = rnd(-4, 4); sacc.ty = rnd(-2, 2); }
            var ks = 1 - Math.exp(-dt * 30); sacc.x += (sacc.tx - sacc.x) * ks; sacc.y += (sacc.ty - sacc.y) * ks;
            if (st.mood !== "sleepy" && st.mood !== "dizzy") { QF.look = sacc.x; QF.lookY = sacc.y; }
            if (!Q.name) return;
            Q.t += dt;
            var t = Q.t, D = QDUR[Q.name], e;
            if (t >= D) { if (Q.name === "yawn" || Q.name === "sneeze") blinkC = 0.16; Q.name = ""; return; }
            switch (Q.name) {
                case "glance": e = bump(t, D, 0.15); QF.look += Q.side * 18 * e; QF.lookY -= 2 * e; break;
                case "double blink": if (once(0)) blinkC = 0.16; if (t > 0.26 && once(1)) blinkC = 0.16; break;
                case "slow blink": e = bump(t, D, 0.45); QF.openL = QF.openR = 1 - 0.97 * e; QF.curve = 0.5 * e; break;
                case "wink": e = bump(t, D, 0.12); if (Q.side > 0) QF.openR = 1 - e; else QF.openL = 1 - e; QF.curve = 0.7 * e; QF.dx = Q.side * 2 * e; break;
                case "yawn": e = bump(t, D, 0.8); QF.gape = 1.6 * e; QF.w = -5 * e; QF.openL = QF.openR = 1 - 0.85 * e; QF.dy = -6 * e; QF.sq = 0.08 * e; break;
                case "hum":
                    e = bump(t, D, 0.3); QF.w = -8 * e; QF.gape = 0.45 * e; QF.dx = Math.sin(t * 4.2) * 4 * e; QF.dy = -Math.abs(Math.sin(t * 4.2)) * 2 * e; QF.openL = QF.openR = 1 - 0.4 * e;
                    for (var n = 0; n < 3; n++) if (t > 0.3 + n && once(n)) notes.push({x: 150 + rnd(-6, 10), y: 128, life: 1.6});
                    break;
                case "sneeze":
                    if (t < 1.1) { e = clamp(t / 1.1, 0, 1); QF.openL = QF.openR = 1 - 0.75 * e; QF.dy = -7 * e; QF.gape = 0.7 * e; QF.w = -4 * e; QF.dx = Math.sin(t * 30) * e; }
                    else { if (once(0)) blinkC = 0.16; e = 1 - clamp((t - 1.1) / 0.6, 0, 1); QF.openL = QF.openR = 1 - e; QF.gape = 0.2 * e; QF.dy = 8 * e; }
                    break;
                case "look up": e = bump(t, D, 0.3); QF.lookY -= 7 * e; QF.look += Q.side * 7 * e; QF.openL = QF.openR = 1 + 0.2 * e; break;
                case "stretch": e = bump(t, D, 0.6); QF.sq = 0.16 * e; QF.cheek = 0.9 * e; QF.curve = 0.5 * e; QF.dy = -4 * e; QF.openL = QF.openR = 1 + e; break;
                case "eye roll": var a = clamp(t / D, 0, 1) * 2 * Math.PI; e = bump(t, D, 0.15); QF.look += Math.sin(a) * 16 * e; QF.lookY -= (1 - Math.cos(a)) * 4 * e; QF.openL = QF.openR = 1 - 0.25 * e; break;
                case "nod": QF.dy = Math.sin(Math.PI * clamp(t / D, 0, 1)) * 6; break;
                case "cheer": e = bump(t, D, 0.2); QF.cheek = e; QF.curve = 0.8 * e; QF.gape = 0.6 * e; QF.dy = -Math.abs(Math.sin(t * 10)) * 6 * e; QF.openL = QF.openR = 1 + e; break;
                case "sigh": e = bump(t, D, 0.5); QF.openL = QF.openR = 1 - 0.6 * e; QF.dy = 5 * e; QF.curve = -0.3 * e; QF.gape = 0.25 * e; QF.w = -6 * e; break;
                case "huff": e = bump(t, D, 0.12); QF.dy = 4 * e; QF.gape = 0.4 * e; QF.w = -7 * e; QF.dx = Math.sin(t * 40) * 1.5 * e; break;
            }
        }

        /* ---- decorations: the KNOMI says which; snow, leaves and fireworks are made here ---- */
        function stepDeco(dt) {
            var k = st.deco === "auto" || st.deco === "off" ? "" : st.deco;
            if (k !== decoKind) { decoKind = k; parts = []; fw = []; }
            var snow = k === "winter" || (k === "holidays" && !st.south);
            var want = snow ? (k === "winter" ? 30 : 24) : k === "spring" ? 14 : k === "autumn" ? 12 : k === "valentine" ? 10 : 0;
            spawnT -= dt;
            if (parts.length < want && spawnT <= 0) {
                spawnT = k === "valentine" ? 0.5 : 0.25;
                var p = {x: rnd(10, 230), y: -8, ph: rnd(0, 6.28), rot: rnd(0, 6.28), vr: rnd(-2, 2)};
                if (snow) { p.kind = "snow"; p.r = rnd(1, 2.4); p.vy = rnd(14, 30); p.col = "#E7EEF4"; }
                else if (k === "spring") { p.kind = "petal"; p.vy = rnd(10, 18); p.col = Math.random() < 0.5 ? "#F8BBD0" : "#F48FB1"; }
                else if (k === "autumn") { p.kind = "leaf"; p.vy = rnd(16, 26); p.col = ["#E65100", "#F9A825", "#BF360C", "#A1887F"][Math.floor(rnd(0, 4))]; }
                else { p.kind = "heart"; p.y = 250; p.vy = -rnd(10, 18); p.r = rnd(3.5, 6); p.col = Math.random() < 0.5 ? "#E53935" : "#F48FB1"; }
                parts.push(p);
            }
            if (!want) parts = [];
            parts.forEach(function (p) { p.ph += dt; p.y += p.vy * dt; p.rot += p.vr * dt; p.x += Math.sin(p.ph * (p.kind === "leaf" ? 2.2 : 1.3)) * (p.kind === "snow" ? 8 : 16) * dt; });
            parts = parts.filter(function (p) { return p.y < 250 && p.y > -20 && p.x > -20 && p.x < 260; });
            if (k === "newyear" || k === "july4") {
                fwT -= dt;
                if (fwT <= 0 && fw.length < 3) {
                    fwT = rnd(0.9, 2.2);
                    var cols = k === "july4" ? ["#E53935", "#F5F5F5", "#42A5F5"] : ["#FFD54F", "#FFB300", "#F5F5F5", "#E53935", "#4FC3F7"];
                    fw.push({x: rnd(50, 190), y: 250, ty: rnd(30, 95), col: cols[Math.floor(rnd(0, cols.length))], sparks: null});
                }
                fw.forEach(function (f) {
                    if (!f.sparks) { f.y -= 180 * dt; if (f.y <= f.ty) { f.sparks = []; for (var i = 0; i < 24; i++) { var a = i / 24 * 6.283, v = rnd(62, 70); f.sparks.push({x: f.x, y: f.y, vx: Math.cos(a) * v, vy: Math.sin(a) * v}); } f.life = 1.1; } }
                    else { f.life -= dt; var dr = Math.exp(-dt * 2.2); f.sparks.forEach(function (s) { s.vx *= dr; s.vy = s.vy * dr + 28 * dt; s.x += s.vx * dt; s.y += s.vy * dt; }); }
                });
                fw = fw.filter(function (f) { return !f.sparks || f.life > 0; });
            } else fw = [];
            shades += ((st.shades ? 1 : 0) - shades) * (1 - Math.exp(-dt * 5));
        }
        function heart(ctx, x, y, r) { ctx.beginPath(); ctx.arc(x - r * 0.5, y, r * 0.55, Math.PI, 0); ctx.arc(x + r * 0.5, y, r * 0.55, Math.PI, 0); ctx.lineTo(x, y + r * 1.1); ctx.closePath(); ctx.fill(); }
        function drawDecoBack(ctx) {
            parts.forEach(function (p) {
                ctx.save(); ctx.translate(p.x, p.y); ctx.fillStyle = p.col; ctx.strokeStyle = p.col;
                if (p.kind === "snow") { ctx.globalAlpha = 0.85; ctx.beginPath(); ctx.arc(0, 0, p.r, 0, 7); ctx.fill(); }
                else if (p.kind === "petal") { ctx.rotate(p.rot); ctx.beginPath(); ctx.ellipse(0, 0, 3.6, 2, 0, 0, 7); ctx.fill(); }
                else if (p.kind === "leaf") { ctx.rotate(p.rot); ctx.beginPath(); ctx.ellipse(0, 0, 5.5, 2.8, 0, 0, 7); ctx.fill(); ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(5, 0); ctx.lineTo(8, 0); ctx.stroke(); }
                else { ctx.globalAlpha = clamp((p.y - 10) / 60, 0, 0.9); heart(ctx, 0, 0, p.r); }
                ctx.restore();
            });
            fw.forEach(function (f) {
                ctx.fillStyle = f.col; ctx.strokeStyle = f.col;
                if (!f.sparks) { ctx.beginPath(); ctx.arc(f.x, f.y, 1.8, 0, 7); ctx.fill(); return; }
                var al = clamp(f.life / 0.7, 0, 1); ctx.lineWidth = 1.4;
                f.sparks.forEach(function (s) { ctx.globalAlpha = al * 0.45; ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(s.x - s.vx * 0.12, s.y - s.vy * 0.12); ctx.stroke(); ctx.globalAlpha = al; ctx.beginPath(); ctx.arc(s.x, s.y, 1.9, 0, 7); ctx.fill(); });
                ctx.globalAlpha = 1;
            });
        }
        function bulbColor(i) {
            if (st.lights === "rainbow") return "hsl(" + Math.round((i * 40 + now * 40) % 360) + ",85%,60%)";
            var pal = {classic: ["#E53935", "#43A047", "#1E88E5", "#FDD835", "#FB8C00"], warm: ["#FFD27A"], theme: [st.c], candy: ["#E53935", "#F5F5F5"]}[st.lights] || ["#E53935"];
            return pal[i % pal.length];
        }
        function bulbLevel(i, n) {
            if (st.anim === "steady") return 1;
            if (st.anim === "chase") return 0.25 + 0.75 * Math.pow(Math.max(0, Math.cos((i / n) * 6.283 * 2 - now * 4)), 2);
            if (st.anim === "breathe") return 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(now * 1.6 + (i % 2) * Math.PI));
            var h = Math.sin(i * 91.7 + Math.floor(now * 3 + i * 0.37) * 13.1) * 43758.5; h -= Math.floor(h); return h < 0.22 ? 0.25 : 1;
        }
        function drawLights(ctx) {
            var hooks = [], N = 6, bulbs = [];
            for (var i = 0; i <= N; i++) { var a = Math.PI * (1.16 + 0.68 * i / N); hooks.push([120 + Math.cos(a) * 116, 122 + Math.sin(a) * 116]); }
            ctx.strokeStyle = "#2E3B2F"; ctx.lineWidth = 1.6; ctx.beginPath();
            for (var j = 0; j < N; j++) {
                var p0 = hooks[j], p1 = hooks[j + 1], mx = (p0[0] + p1[0]) / 2, my = (p0[1] + p1[1]) / 2, dx = 120 - mx, dy = 122 - my, dl = Math.hypot(dx, dy);
                var c = [mx + dx / dl * 11, my + dy / dl * 11];
                if (j === 0) ctx.moveTo(p0[0], p0[1]);
                ctx.quadraticCurveTo(c[0], c[1], p1[0], p1[1]);
                [0.3, 0.7].forEach(function (t) { var u = 1 - t; bulbs.push([u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1], Math.atan2(dy, dx)]); });
            }
            ctx.stroke();
            bulbs.forEach(function (b, i) {
                var lv = bulbLevel(i, bulbs.length), col = bulbColor(i);
                ctx.save(); ctx.translate(b[0], b[1]); ctx.rotate(b[2] - Math.PI / 2);
                ctx.fillStyle = "#2E3B2F"; ctx.fillRect(-1.8, -1, 3.6, 3); ctx.fillStyle = col;
                ctx.globalAlpha = 0.22 * lv; ctx.beginPath(); ctx.arc(0, 7, 8.5, 0, 7); ctx.fill();
                ctx.globalAlpha = 0.35 + 0.65 * lv; ctx.beginPath(); ctx.ellipse(0, 6.5, 3.4, 4.8, 0, 0, 7); ctx.fill();
                ctx.restore();
            });
            ctx.globalAlpha = 1;
        }
        function drawBats(ctx) {
            for (var i = 0; i < 3; i++) {
                var t = (now * 0.09 + i / 3) % 1, x = -20 + t * 280, y = 46 + i * 16 + Math.sin(now * 1.7 + i * 2) * 10, f = Math.sin(now * 14 + i * 3);
                ctx.lineWidth = 1.8; ctx.beginPath(); ctx.arc(x, y, 2.4, 0, 7); ctx.fill();
                ctx.beginPath(); ctx.moveTo(x - 2, y); ctx.lineTo(x - 6, y - 3 - f * 4); ctx.lineTo(x - 11, y - f * 2); ctx.moveTo(x + 2, y); ctx.lineTo(x + 6, y - 3 - f * 4); ctx.lineTo(x + 11, y - f * 2); ctx.stroke();
            }
        }
        function drawFlower(ctx, x, y) {
            ctx.fillStyle = "#F48FB1"; for (var i = 0; i < 5; i++) { var a = i / 5 * 6.283 + 0.3; ctx.beginPath(); ctx.arc(x + Math.cos(a) * 4.6, y + Math.sin(a) * 4.6, 3.4, 0, 7); ctx.fill(); }
            ctx.fillStyle = "#FDD835"; ctx.beginPath(); ctx.arc(x, y, 2.8, 0, 7); ctx.fill();
        }
        function drawShades(ctx, cx, ey, sx) {
            if (shades < 0.02) return;
            var drop = (1 - shades) * -40; ctx.globalAlpha = Math.min(1, shades * 1.5);
            ctx.fillStyle = st.c; ctx.strokeStyle = st.c; ctx.lineWidth = 3;
            [-1, 1].forEach(function (s) { var x = cx + s * 54 * sx; ctx.beginPath(); ctx.moveTo(x - 31, ey - 11 + drop); ctx.lineTo(x + 31, ey - 11 + drop); ctx.quadraticCurveTo(x + 30, ey + 15 + drop, x, ey + 15 + drop); ctx.quadraticCurveTo(x - 30, ey + 15 + drop, x - 31, ey - 11 + drop); ctx.fill(); });
            ctx.beginPath(); ctx.moveTo(cx - 24 * sx, ey - 8 + drop); ctx.quadraticCurveTo(cx, ey - 14 + drop, cx + 24 * sx, ey - 8 + drop); ctx.stroke();
            ctx.globalAlpha = 1;
        }
        function drawHat(ctx, cx, top) {
            var hat = st.hat;
            ctx.fillStyle = st.c;
            if (hat === 1) { ctx.beginPath(); ctx.moveTo(cx - 22, top); ctx.lineTo(cx + 22, top); ctx.lineTo(cx + 6, top - 46); ctx.fill(); ctx.strokeStyle = "#000"; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(cx - 14, top - 14); ctx.lineTo(cx + 15, top - 14); ctx.moveTo(cx - 6, top - 30); ctx.lineTo(cx + 11, top - 30); ctx.stroke(); ctx.beginPath(); ctx.arc(cx + 6, top - 48, 5, 0, 7); ctx.fill(); }
            else if (hat === 2) { ctx.beginPath(); ctx.moveTo(cx - 30, top); ctx.lineTo(cx + 30, top); ctx.lineTo(cx + 44, top - 34); ctx.fill(); ctx.fillStyle = "#E7EEF4"; ctx.fillRect(cx - 34, top - 2, 68, 10); ctx.beginPath(); ctx.arc(cx + 46, top - 34, 7, 0, 7); ctx.fill(); }
            else if (hat === 3) { ctx.beginPath(); ctx.moveTo(cx - 16, top - 2); ctx.lineTo(cx + 16, top - 2); ctx.lineTo(cx + 12, top - 58); ctx.fill(); ctx.beginPath(); ctx.ellipse(cx, top, 42, 6, 0, 0, 7); ctx.fill(); ctx.strokeStyle = "#000"; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(cx - 15, top - 10); ctx.lineTo(cx + 15, top - 10); ctx.stroke(); }
        }

        /* ---- the face (same drawing as the KNOMI) ---- */
        function eye(ctx, ex, ey, side, sx, open, lookY) {
            var L = 26 * sx, r = 11 * E.size, slide = clamp(pup.x * 1.6 + look + QF.look, -(L - r), L - r);
            var squash = clamp(1 + pup.y / 22, 0.55, 1.5);
            var ox = Math.cos(now * 6 * side) * 9 * E.orbit, oy = Math.sin(now * 6 * side) * 4 * E.orbit;
            var px = ex + slide + ox, py = ey + oy + lookY, ry = r * squash, lid = py + ry - open * 2 * ry, slope = -side * 0.28 * E.tilt;
            ctx.save(); ctx.beginPath();
            ctx.moveTo(ex - L - 20, lid + slope * (-L - 20)); ctx.lineTo(ex + L + 20, lid + slope * (L + 20));
            ctx.lineTo(ex + L + 20, py + 60); ctx.lineTo(ex - L - 20, py + 60); ctx.closePath();
            if (E.cheek > 0.01) { var cyc = py + r * 2.4 - E.cheek * 1.55 * r; ctx.moveTo(px + r * 1.5, cyc); ctx.arc(px, cyc, r * 1.5, 0, Math.PI * 2, true); }
            ctx.clip("evenodd"); ctx.beginPath(); ctx.ellipse(px, py, r, ry, 0, 0, Math.PI * 2); ctx.fill(); ctx.restore();
            var k = clamp((open - 0.5) / 0.3, 0, 1);
            if (k < 0.98) {
                var x0 = lerp(ex - L, px, k), x1 = lerp(ex + L, px, k);
                ctx.globalAlpha = 1 - k * 0.6; ctx.lineWidth = 5; ctx.beginPath();
                ctx.moveTo(x0, lid + slope * (x0 - ex)); ctx.lineTo(x1, lid + slope * (x1 - ex)); ctx.stroke(); ctx.globalAlpha = 1;
            }
        }
        function mouth(ctx, mx, my) {
            var N = 24, top = [], bot = [], w = E.w, z = st.mood === "shivering" && Math.sin(now * 38) > 0 ? 0.5 : 0;
            for (var i = 0; i <= N; i++) {
                var t = -1 + 2 * i / N, x = mx + t * w, env = 1 - t * t;
                var y = my + E.curve * 7 * env + E.omega * 5 * Math.abs(Math.sin(Math.PI * t)) +
                    E.zig * 3 * (2 * Math.abs(2 * ((t * 3 + z) - Math.floor(t * 3 + z + 0.5))) - 1) + E.wave * 3 * Math.sin(t * 5 + now * 9);
                top.push([x, y]); bot.push([x, y + E.gape * 9 * Math.pow(env, 0.7)]);
            }
            ctx.lineWidth = 4; ctx.beginPath();
            top.forEach(function (p, i) { i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); });
            if (E.gape > 0.06) { for (var j = bot.length - 1; j >= 0; j--) ctx.lineTo(bot[j][0], bot[j][1]); ctx.closePath(); ctx.fill(); }
            ctx.stroke();
        }

        function frame(t) {
            requestAnimationFrame(frame);
            var cv = document.getElementById("knomi_coaster_canvas");
            if (!cv || document.hidden || !cv.offsetParent) { last = t; return; }
            var dt = Math.min(0.1, (t - last) / 1000 || 0); last = t; now += dt; moodT += dt;
            var mood = st.mood;
            // expression: the mood, tinted by how it feels (same as the firmware)
            var target = (M[mood] || M.calm).slice(), kk = 1 - Math.exp(-dt * 9);
            var hv = clamp(st.h + (st.pr ? 0.25 : 0), -1, 1);
            if (["calm", "riding", "focused", "cooling off", "windy", "almost there", "bored"].indexOf(mood) >= 0) {
                target[5] = clamp(target[5] + 0.55 * hv, -0.9, 1); target[0] = clamp(target[0] + 0.08 * hv, 0.2, 1);
                if (hv < 0) { target[10] += 0.7 * hv; target[6] *= 1 + hv; }
            }
            KEYS.forEach(function (k, i) { E[k] += (target[i] - E[k]) * kk; });
            if (mood === "hungry") E.gape = 0.9 * Math.abs(Math.sin(now * 5));
            // head and pupils: follow what the KNOMI reports (a little smoothed), or sit still
            if (st.live && Date.now() - st.liveAt > 3000) st.live = false;
            var fk = 1 - Math.exp(-dt * 12);
            head.x += ((st.live ? st.hx : 0) - head.x) * fk; head.y += ((st.live ? st.hy : 0) - head.y) * fk; head.s += ((st.live ? st.hs : 0) - head.s) * fk;
            pup.x += ((st.live ? st.px : 0) - pup.x) * fk; pup.y += ((st.live ? st.py : 0) - pup.y) * fk;
            wanderT -= dt; if (wanderT <= 0) { wander = rnd(-12, 12); wanderT = rnd(1.2, 3.5); }
            var want = st.live ? st.look : mood === "bored" ? Math.sin(now * 1.3) * 12 : mood === "lonely" ? Math.sin(now * 0.7) * 14 :
                mood === "confused" ? (now % 2.4 < 1.2 ? -10 : 10) : mood === "mad" ? -10 : (mood === "calm" || mood === "riding" || mood === "sleepy") ? wander : 0;
            look += (want - look) * kk;
            if (blinkC > 0) blinkC -= dt; else { blinkT -= dt; if (blinkT <= 0 && mood !== "sleepy" && mood !== "dizzy") { blinkC = 0.16; blinkT = rnd(2.2, 6); } }
            stepQuirk(dt); stepDeco(dt);
            notes.forEach(function (n) { n.life -= dt; n.y -= 22 * dt; n.x += Math.sin(n.life * 5) * 12 * dt; }); notes = notes.filter(function (n) { return n.life > 0; });
            confetti.forEach(function (p) { p.vy += 420 * dt; p.x += p.vx * dt; p.y += p.vy * dt; p.r += p.vr * dt; p.life -= dt; }); confetti = confetti.filter(function (p) { return p.life > 0; });

            var ctx = cv.getContext("2d"), fc = st.c || "#C02F30";
            ctx.setTransform(cv.width / 240, 0, 0, cv.height / 240, 0, 0);
            ctx.fillStyle = "#000"; ctx.fillRect(0, 0, 240, 240);
            drawDecoBack(ctx);
            ctx.strokeStyle = fc; ctx.fillStyle = fc; ctx.lineCap = "round"; ctx.lineJoin = "round";
            var jit = mood === "shocked" ? 1.5 : mood === "heating up" ? 0.4 + st.heat * 1.8 : mood === "hanging on" ? 0.8 : mood === "windy" ? 0.6 : 0;
            var cx = 120 + head.x + rnd(-jit, jit) + QF.dx, cy = 118 + head.y + rnd(-jit, jit) + QF.dy;
            var hsq = head.s + QF.sq, sx = 1 - hsq * 0.5, sy = 1 + hsq;
            if (mood === "giggle") cy -= Math.abs(Math.sin(now * 14)) * 6;
            if (mood === "ready") cy -= Math.abs(Math.sin(now * 6)) * 4;
            if (mood === "celebrate") cy -= Math.abs(Math.sin(now * 9)) * 8;
            cy += Math.sin(now * 1.6) * 1.5 * (1 - clamp(E.open * 4, 0, 1));
            var tl = 0, tr = 0;
            if (st.act === 3) { var q = Math.sin(now * 1.4) * 7; tl = q; tr = -q; }          // leveling
            if (st.act === 2) cy += Math.abs(Math.sin(now * Math.PI * 1.6)) * 5;           // probing
            if (st.act === 6) cx += Math.sin(now * 14) * 6;                                 // cleaning
            var blinkK = blinkC > 0 ? Math.sin(Math.PI * (1 - blinkC / 0.16)) : 0, open = E.open * (1 - blinkK);
            var keep = {curve: E.curve, w: E.w, cheek: E.cheek, gape: E.gape, omega: E.omega};
            E.curve += QF.curve; E.w = Math.max(4, E.w + QF.w); E.cheek = Math.max(E.cheek, QF.cheek); E.gape = Math.max(E.gape, QF.gape);
            E.omega *= 1 - clamp(QF.gape / 0.4, 0, 1);
            eye(ctx, cx - 54 * sx, cy - 14 * sy + tl, -1, sx, clamp(open * QF.openL, 0, 1.1), QF.lookY);
            eye(ctx, cx + 54 * sx, cy - 14 * sy + tr, 1, sx, clamp(open * QF.openR, 0, 1.1), QF.lookY);
            mouth(ctx, cx, cy + 22 * sy);
            for (var k2 in keep) E[k2] = keep[k2];
            notes.forEach(function (n) { ctx.globalAlpha = clamp(n.life / 0.6, 0, 1); ctx.beginPath(); ctx.ellipse(n.x, n.y, 4, 3, 0, 0, 7); ctx.fill(); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(n.x + 3, n.y); ctx.lineTo(n.x + 3, n.y - 13); ctx.lineTo(n.x + 8, n.y - 9); ctx.stroke(); });
            ctx.globalAlpha = 1;
            drawShades(ctx, cx, cy - 14 * sy, sx);
            ctx.fillStyle = fc; ctx.strokeStyle = fc;
            if (decoKind === "spring") drawFlower(ctx, cx - 38 * sx, cy - 50 * sy);
            if (decoKind === "halloween") { ctx.fillStyle = fc; ctx.strokeStyle = fc; drawBats(ctx); }
            if (decoKind === "holidays") drawLights(ctx);
            drawHat(ctx, cx, cy - 50 * sy);
            ctx.fillStyle = fc; ctx.strokeStyle = fc;
            if (mood === "heating up") {
                for (var i = 0; i < 3; i++) { var qq = (now * (0.8 + st.heat) + i / 3) % 1, sx2 = cx + (i - 1) * 26 * sx + Math.sin(now * 3 + i) * 3, sy2 = cy - 52 - qq * 26;
                    ctx.globalAlpha = Math.sin(qq * Math.PI) * (0.4 + 0.6 * st.heat); ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(sx2, sy2); ctx.lineTo(sx2, sy2 - 4 - 6 * st.heat * (1 - qq)); ctx.stroke(); }
                ctx.globalAlpha = 1;
            }
            if (mood === "sleepy") { ctx.font = "700 16px sans-serif"; for (var z = 0; z < 3; z++) { var zz = (now * 0.45 + z / 3) % 1; ctx.globalAlpha = Math.sin(zz * Math.PI) * clamp(moodT / 1.5, 0, 1); ctx.fillText("z", 160 + zz * 26, 96 - zz * 36); } ctx.globalAlpha = 1; }
            confetti.forEach(function (p) { ctx.globalAlpha = Math.min(1, p.life); ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(p.x - Math.cos(p.r) * 3, p.y - Math.sin(p.r) * 3); ctx.lineTo(p.x + Math.cos(p.r) * 3, p.y + Math.sin(p.r) * 3); ctx.stroke(); });
            ctx.globalAlpha = 1;
        }
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: CoasterViewModel,
        dependencies: [],
        elements: ["#knomi_coaster"]
    });
});
