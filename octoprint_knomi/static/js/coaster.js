// Coaster, mirrored from the KNOMI: same face and expressions as the firmware (knomi_coaster.cpp).
// The KNOMI sends its mood; the face eases between expressions, blinks and looks around here.
$(function () {
    var RED = "#C02F30";
    // open, size, cheek, orbit, w, curve, omega, gape, zig, wave, tilt
    var M = {
        "calm": [0.5, 1, 0, 0, 14, 0, 1, 0, 0, 0, 0], "riding": [0.56, 1, 0, 0, 14, 0.3, 0.7, 0, 0, 0, 0],
        "excited": [0.85, 1.05, 0, 0, 12, 0.7, 0, 0.9, 0, 0, 0], "screaming": [1, 1.3, 0, 0, 8, 0, 0, 1.7, 0, 0, 0],
        "startled": [1, 1.2, 0, 0, 6, 0, 0, 1.1, 0, 0, 0], "elevator": [0.75, 1.05, 0, 0, 7, 0, 0, 0.6, 0, 0, 0],
        "sleepy": [0.02, 1, 0, 0, 11, 0, 1, 0, 0, 0, 0], "bored": [0.32, 1, 0, 0, 9, 0, 0, 0, 0, 0, 0],
        "shivering": [0.42, 1, 0, 0, 14, 0, 0, 0, 1, 0, 0], "dizzy": [0.5, 1, 0, 1, 14, 0, 0, 0, 0, 1, 0],
        "giggle": [1, 1.05, 1, 0, 12, 0.8, 0, 0.8, 0, 0, 0], "celebrate": [1, 1.1, 1, 0, 15, 0.9, 0, 1, 0, 0, 0],
        "ready": [1, 1.1, 0, 0, 13, 0.8, 0, 0.5, 0, 0, 0], "sad": [0.35, 1, 0, 0, 12, -0.9, 0, 0, 0, 0, -0.7],
        "shocked": [1, 0.75, 0, 0, 10, 0, 0, 0.35, 0, 0.8, -0.4], "lonely": [0.45, 1, 0, 0, 9, -0.6, 0, 0, 0, 0, -0.5],
        "confused": [0.55, 1, 0, 0, 10, 0, 0, 0, 0, 0.5, 0.3], "heating up": [0.42, 0.95, 0, 0, 13, 0, 0, 0, 0.8, 0, 1],
        "cooling off": [0.3, 1, 0, 0, 12, 0.5, 0.4, 0, 0, 0, 0], "focused": [0.28, 0.9, 0, 0, 7, 0, 0, 0, 0, 0, 0],
        "almost there": [0.8, 1.05, 0, 0, 12, 0.5, 0, 0.2, 0, 0, 0], "hungry": [0.7, 1, 0, 0, 8, 0, 0, 0.9, 0, 0, 0],
        "windy": [0.3, 1, 0, 0, 10, 0, 0, 0, 0, 0.6, 0], "hanging on": [0.9, 0.9, 0, 0, 10, 0, 0, 0, 0.6, 0, -0.3],
        "bracing": [0.08, 1, 0, 0, 10, 0, 0, 0, 0.5, 0, -0.3], "leveling": [0.5, 1, 0, 0, 12, 0, 0, 0, 0, 0, 0],
        "scrubbing": [0.8, 1, 0.6, 0, 12, 0.5, 0, 0, 0, 0.6, 0],
        "whee": [1, 1.15, 0, 0, 13, 0.8, 0, 1.3, 0, 0, 0],
        "mad": [0.36, 0.95, 0, 0, 12, -0.35, 0, 0, 0.3, 0, 1]
    };
    var KEYS = ["open", "size", "cheek", "orbit", "w", "curve", "omega", "gape", "zig", "wave", "tilt"];
    function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
    function lerp(a, b, t) { return a + (b - a) * t; }

    function CoasterViewModel(parameters) {
        var self = this;
        self.moodLabel = ko.observable("");
        self.reportLine = ko.observable("");
        self.seen = ko.observable(false);
        var mood = "calm", hat = 0, E = {}, look = 0, wander = 0, wanderT = 0, blinkT = 3, blinkC = 0, now = 0, last = 0;
        KEYS.forEach(function (k, i) { E[k] = M.calm[i]; });

        self.apply = function (c) {
            if (!c || !c.mood) return;
            self.seen(true);
            mood = M[c.mood] ? c.mood : "calm";
            hat = c.hat || 0;
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
        };
        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin === "knomi" && data && data.coaster) self.apply(data.coaster);
        };

        function eye(ctx, ex, ey, side, open) {
            var L = 26, r = 11 * E.size, slide = clamp(look, -(L - r), L - r);
            var ox = Math.cos(now * 6 * side) * 9 * E.orbit, oy = Math.sin(now * 6 * side) * 4 * E.orbit;
            var px = ex + slide + ox, py = ey + oy, lid = py + r - open * 2 * r, slope = -side * 0.28 * E.tilt;
            ctx.save(); ctx.beginPath();
            ctx.moveTo(ex - L - 20, lid + slope * (-L - 20)); ctx.lineTo(ex + L + 20, lid + slope * (L + 20));
            ctx.lineTo(ex + L + 20, py + 60); ctx.lineTo(ex - L - 20, py + 60); ctx.closePath();
            if (E.cheek > 0.01) { var cyc = py + r * 2.4 - E.cheek * 1.55 * r; ctx.moveTo(px + r * 1.5, cyc); ctx.arc(px, cyc, r * 1.5, 0, Math.PI * 2, true); }
            ctx.clip("evenodd"); ctx.beginPath(); ctx.ellipse(px, py, r, r, 0, 0, Math.PI * 2); ctx.fill(); ctx.restore();
            var k = clamp((open - 0.5) / 0.3, 0, 1);
            if (k < 0.98) {
                var x0 = lerp(ex - L, px, k), x1 = lerp(ex + L, px, k);
                ctx.globalAlpha = 1 - k * 0.6; ctx.lineWidth = 5; ctx.beginPath();
                ctx.moveTo(x0, lid + slope * (x0 - ex)); ctx.lineTo(x1, lid + slope * (x1 - ex)); ctx.stroke(); ctx.globalAlpha = 1;
            }
        }
        function mouth(ctx, mx, my) {
            var N = 24, top = [], bot = [], w = E.w;
            for (var i = 0; i <= N; i++) {
                var t = -1 + 2 * i / N, x = mx + t * w, env = 1 - t * t;
                var y = my + E.curve * 7 * env + E.omega * 5 * Math.abs(Math.sin(Math.PI * t)) +
                    E.zig * 3 * (2 * Math.abs(2 * ((t * 3) - Math.floor(t * 3 + 0.5))) - 1) + E.wave * 3 * Math.sin(t * 5 + now * 9);
                top.push([x, y]); bot.push([x, y + E.gape * 9 * Math.pow(env, 0.7)]);
            }
            ctx.lineWidth = 4; ctx.beginPath();
            top.forEach(function (p, i) { i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); });
            if (E.gape > 0.06) { for (var j = bot.length - 1; j >= 0; j--) ctx.lineTo(bot[j][0], bot[j][1]); ctx.closePath(); ctx.fill(); }
            ctx.stroke();
        }
        function drawHat(ctx, cx, top) {
            ctx.fillStyle = RED;
            if (hat === 1) { ctx.beginPath(); ctx.moveTo(cx - 22, top); ctx.lineTo(cx + 22, top); ctx.lineTo(cx + 6, top - 46); ctx.fill(); ctx.beginPath(); ctx.arc(cx + 6, top - 48, 5, 0, 7); ctx.fill(); }
            else if (hat === 2) { ctx.beginPath(); ctx.moveTo(cx - 30, top); ctx.lineTo(cx + 30, top); ctx.lineTo(cx + 44, top - 34); ctx.fill(); ctx.fillRect(cx - 34, top - 2, 68, 10); ctx.beginPath(); ctx.arc(cx + 46, top - 34, 7, 0, 7); ctx.fill(); }
            else if (hat === 3) { ctx.beginPath(); ctx.moveTo(cx - 16, top - 2); ctx.lineTo(cx + 16, top - 2); ctx.lineTo(cx + 12, top - 58); ctx.fill(); ctx.beginPath(); ctx.ellipse(cx, top, 42, 6, 0, 0, 7); ctx.fill(); }
        }
        function frame(t) {
            requestAnimationFrame(frame);
            var cv = document.getElementById("knomi_coaster_canvas");
            if (!cv || document.hidden || !cv.offsetParent) { last = t; return; }
            var dt = Math.min(0.1, (t - last) / 1000 || 0); last = t; now += dt;
            var target = M[mood] || M.calm, kk = 1 - Math.exp(-dt * 9);
            KEYS.forEach(function (k, i) { E[k] += (target[i] - E[k]) * kk; });
            wanderT -= dt; if (wanderT <= 0) { wander = (Math.random() - 0.5) * 24; wanderT = 1.2 + Math.random() * 2.3; }
            var wantLook = (mood === "calm" || mood === "riding" || mood === "sleepy") ? wander : mood === "bored" ? Math.sin(now * 1.3) * 12 : mood === "lonely" ? Math.sin(now * 0.7) * 14 : 0;
            look += (wantLook - look) * kk;
            if (blinkC > 0) blinkC -= dt; else { blinkT -= dt; if (blinkT <= 0 && mood !== "sleepy" && mood !== "dizzy") { blinkC = 0.16; blinkT = 2.2 + Math.random() * 4; } }
            if (mood === "hungry") E.gape = 0.9 * Math.abs(Math.sin(now * 5));
            var ctx = cv.getContext("2d");
            ctx.setTransform(1.5, 0, 0, 1.5, 0, 0);
            ctx.fillStyle = "#000"; ctx.fillRect(0, 0, 240, 240);
            ctx.strokeStyle = RED; ctx.fillStyle = RED; ctx.lineCap = "round"; ctx.lineJoin = "round";
            var jit = (mood === "shocked" ? 1.5 : mood === "heating up" ? 1.2 : 0), cx = 120 + (Math.random() - 0.5) * jit * 2, cy = 118 + (Math.random() - 0.5) * jit * 2;
            if (mood === "giggle" || mood === "celebrate" || mood === "ready") cy -= Math.abs(Math.sin(now * 9)) * 6;
            var blinkK = blinkC > 0 ? Math.sin(Math.PI * (1 - blinkC / 0.16)) : 0, open = E.open * (1 - blinkK);
            eye(ctx, cx - 54, cy - 14, -1, open); eye(ctx, cx + 54, cy - 14, 1, open);
            mouth(ctx, cx, cy + 22);
            drawHat(ctx, cx, cy - 50);
            if (mood === "sleepy") { ctx.font = "700 16px sans-serif"; for (var i = 0; i < 3; i++) { var z = (now * 0.45 + i / 3) % 1; ctx.globalAlpha = Math.sin(z * Math.PI); ctx.fillText("z", 160 + z * 26, 92 - z * 36); } ctx.globalAlpha = 1; }
        }
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: CoasterViewModel,
        dependencies: [],
        elements: ["#knomi_coaster"]
    });
});
