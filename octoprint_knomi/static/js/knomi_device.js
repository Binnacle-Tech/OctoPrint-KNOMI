/* The KNOMI's own settings, drawn from the description it serves at /settings.json (see the firmware's
 * settings_schema.cpp for the format). Nothing here knows which settings exist: the KNOMI can add, move or
 * reword them and this page follows. Everything goes through the plugin's /k/ route, which reaches the
 * KNOMI over WiFi when it's on and over Bluetooth when it isn't. */
(function () {
    var BASE = (window.BASEURL || "/") + "plugin/knomi/";
    var K = BASE + "k/";

    function token() {
        var m = document.cookie.match(/(?:^|; )csrf_token[^=]*=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : "";
    }
    function req(method, path, body, ctype) {
        var h = {};
        if (method !== "GET") h["X-CSRF-Token"] = token();
        if (ctype) h["Content-Type"] = ctype;
        return fetch(K + path, {method: method, body: body, headers: h, credentials: "same-origin"});
    }
    function textOf(html) {
        var d = document.createElement("div");
        d.innerHTML = html;
        d.querySelectorAll("script,style,nav,head").forEach(function (e) { e.remove(); });
        return (d.textContent || "").replace(/\s+/g, " ").trim();
    }
    // a KNOMI answer: {"ok","title","note"} JSON, plain text, or (older handlers, errors) a page
    function answer(r) {
        return r.text().then(function (t) {
            var j = null;
            try { j = JSON.parse(t); } catch (e) { /* not JSON */ }
            var msg;
            if (j && j.title !== undefined) msg = j.title + (j.note ? ": " + j.note : "");
            else if (/^\s*</.test(t)) msg = textOf(t).slice(0, 300);
            else msg = t.slice(0, 300) || (r.ok ? "Done" : "Failed");
            if (!r.ok) throw new Error(msg || ("HTTP " + r.status));
            return msg;
        });
    }
    function el(tag, attrs, kids) {
        var e = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            if (k === "text") e.textContent = attrs[k];
            else if (k === "style") e.setAttribute("style", attrs[k]);
            else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
            else if (attrs[k] !== undefined && attrs[k] !== null && attrs[k] !== false) e.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
        });
        (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
        return e;
    }
    var uid = 0;

    function KnomiDevice(root) {
        this.root = root;
        this.schema = null;
        this.open = {};   // section id -> shown (kept across reloads)
    }

    KnomiDevice.prototype.load = function () {
        var self = this;
        self.root.innerHTML = "";
        self.root.appendChild(el("div", {"class": "muted", style: "padding:8px 0"}, [el("i", {"class": "fa fa-spinner fa-spin"}), " Reading the KNOMI's settings…"]));
        return req("GET", "settings.json").then(function (r) {
            if (!r.ok) return answer(r);
            return r.json();
        }).then(function (s) {
            if (!s || !s.sections) throw new Error("The KNOMI didn't send its settings (firmware older than OP45?)");
            self.schema = s;
            self.render();
        }).catch(function (e) {
            self.root.innerHTML = "";
            self.root.appendChild(el("div", {"class": "alert alert-error"}, [
                "Couldn't read the KNOMI's settings: " + e.message + " ",
                el("button", {"class": "btn btn-small", onclick: function () { self.load(); }}, ["Try again"])]));
            // firmware before OP45 doesn't describe its settings: offer the update that brings it
            self.root.appendChild(el("div", {"class": "well well-small"}, [self.firmwareField({
                l: "Update the KNOMI (KNOMI 2)", repo: "Binnacle-Tech/KNOMI", asset: "knomiv2-octoprint-firmware.bin",
                h: "Firmware older than OP45 can't show its settings here. This installs the latest release over WiFi or Bluetooth."})]));
        });
    };

    KnomiDevice.prototype.render = function () {
        var self = this, s = self.schema;
        self.root.innerHTML = "";
        self.root.appendChild(el("div", {"class": "muted", style: "margin-bottom:8px"}, [
            "KNOMI " + (s.fw || "") + " · " + (s.board || "") + " ",
            el("button", {"class": "btn btn-mini", onclick: function () { self.load(); }}, [el("i", {"class": "fa fa-refresh"}), " Reload"])]));
        s.sections.forEach(function (sec) { self.root.appendChild(self.section(sec)); });
    };

    KnomiDevice.prototype.section = function (sec) {
        var self = this;
        var body = el("div", {style: "padding:4px 0 2px"});
        var status = el("span", {"class": "muted", style: "margin-left:8px"});
        var inner = el("div", {style: self.open[sec.id] ? "" : "display:none"}, [
            sec.h ? el("div", {"class": "muted", text: sec.h, style: "margin:6px 0"}) : null, body]);
        var caret = el("i", {"class": "fa fa-fw " + (self.open[sec.id] ? "fa-caret-down" : "fa-caret-right")});
        var box = el("div", {"class": "well well-small", style: "margin-bottom:8px"}, [
            el("div", {style: "display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none", onclick: function () {
                self.open[sec.id] = !self.open[sec.id];
                inner.style.display = self.open[sec.id] ? "" : "none";
                caret.className = "fa fa-fw " + (self.open[sec.id] ? "fa-caret-down" : "fa-caret-right");
            }}, [caret, el("b", {text: sec.title, style: "font-size:15px"}),
                sec.status ? el("span", {"class": "label", text: sec.status}) : null]),
            inner]);
        var inputs = [];
        var row = null, rowG = null;
        (sec.fields || []).forEach(function (f) {
            var w = self.field(sec, f, status, inputs);
            if (!w) return;
            if (f.g && f.g === rowG) { row.appendChild(w); return; }
            rowG = f.g || null;
            row = el("div", {style: "display:flex;flex-wrap:wrap;gap:6px 16px;align-items:flex-end;margin-bottom:8px"}, [w]);
            body.appendChild(row);
        });
        var foot = el("div", {style: "display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:6px"});
        if (sec.save) {
            foot.appendChild(el("button", {"class": "btn btn-primary btn-small", onclick: function () {
                self.post(sec, sec.post, self.collect(sec, inputs), status, sec.json);
            }}, [sec.save]));
        }
        (sec.actions || []).forEach(function (a) {
            foot.appendChild(el("button", {"class": "btn btn-small" + (a.danger ? " btn-danger" : ""), onclick: function () {
                if (a.confirm && !window.confirm(a.confirm)) return;
                var data = a.fields ? self.collect(sec, inputs) : {};
                Object.keys(a.data || {}).forEach(function (k) { data[k] = a.data[k]; });
                self.post(sec, a.post, data, status, false);
            }}, [a.l]));
        });
        if (foot.childNodes.length) { foot.appendChild(status); inner.appendChild(foot); }
        else body.appendChild(status);
        return box;
    };

    // one field's widget; editable ones are pushed onto inputs as {f, get()}
    KnomiDevice.prototype.field = function (sec, f, status, inputs) {
        var self = this, id = "kd" + (++uid), input = null, extra = null;
        var v = f.v === undefined || f.v === null ? "" : String(f.v);
        function wrap(kids, wide) {
            return el("div", {style: "display:flex;flex-direction:column;min-width:" + (wide ? "260px" : "120px") + ";max-width:100%"}, kids);
        }
        function label() { return f.l ? el("label", {"for": id, text: f.l, style: "margin-bottom:2px;font-weight:600"}) : null; }
        function hint() { return f.h ? el("span", {"class": "muted", text: f.h, style: "font-size:12px;max-width:420px"}) : null; }
        switch (f.t) {
            case "hidden":
                inputs.push({f: f, get: function () { return f.v; }});
                return null;
            case "tz":
                inputs.push({f: f, get: function () { return -new Date().getTimezoneOffset(); }});
                return null;
            case "info":
                return wrap([el("span", {text: f.l, style: "font-weight:600"}), el("span", {text: v || "—"}), hint()]);
            case "text":
                input = el("input", {type: "text", id: id, value: v, maxlength: f.max, placeholder: f.ph, "class": "input-large", disabled: f.ro});
                if (f.list && f.list.length) {
                    var dl = el("datalist", {id: id + "l"}, f.list.map(function (o) { return el("option", {value: o[0], label: o[1]}); }));
                    input.setAttribute("list", id + "l");
                    extra = dl;
                }
                break;
            case "password":
                input = el("input", {type: "password", id: id, maxlength: f.max, autocomplete: "new-password", "class": "input-large",
                    placeholder: f.set ? "set (type to change)" : "not set"});
                break;
            case "number":
                input = el("input", {type: "number", id: id, min: f.min, max: f.max, step: f.step || "any", "class": "input-small", disabled: f.ro});
                input.value = v;   // after min/max/step, or the browser snaps it to the defaults
                break;
            case "range":
                var out = el("span", {"class": "muted", text: v, style: "margin-left:6px"});
                input = el("input", {type: "range", id: id, min: f.min, max: f.max, step: f.step || "any", disabled: f.ro,
                    oninput: function () { out.textContent = input.value; }});
                input.value = v;
                return (inputs.push({f: f, get: function () { return input.value; }}),
                        wrap([label(), el("div", {style: "display:flex;align-items:center"}, [input, out]), hint()], true));
            case "select":
                input = el("select", {id: id, disabled: f.ro, "class": "input-xlarge"}, (f.o || []).map(function (o) {
                    return el("option", {value: o[0], text: o[1], selected: String(o[0]) === v});
                }));
                break;
            case "check":
                input = el("input", {type: "checkbox", id: id, checked: !!f.v, disabled: f.ro});
                inputs.push({f: f, get: function () { return input.checked; }});
                return wrap([el("label", {"class": "checkbox", "for": id}, [input, " " + f.l]), hint()], true);
            case "color":
                input = el("input", {type: "color", id: id, value: v || "#c02f30", disabled: f.ro, style: "width:64px;height:30px;padding:0"});
                break;
            case "file":
                return self.fileField(sec, f, id, status);
            case "view":
                return self.viewField(f);
            case "link":
                return wrap([el("a", {"class": "btn btn-small", href: K + f.src, target: "_blank", rel: "noopener"}, [el("i", {"class": "fa fa-download"}), " " + f.l]), hint()], true);
            case "firmware":
                return self.firmwareField(f);
            default:
                return null;   // a kind of field this plugin doesn't know yet
        }
        if (!f.ro) inputs.push({f: f, get: function () { return input.value; }});
        return wrap([label(), input, extra, hint()], f.t === "text" || f.t === "password" || f.t === "select");
    };

    KnomiDevice.prototype.collect = function (sec, inputs) {
        var data = {};
        inputs.forEach(function (i) {
            var f = i.f, val = i.get();
            if (f.t === "password" && !val) return;   // unchanged: never sent back
            if (sec.json) {
                if (f.t === "number" || f.t === "range") val = Number(val);
                else if (f.t === "select" && typeof f.v === "number") val = Number(val);
                data[f.n] = val;
            } else if (f.t === "check") {
                if (val) data[f.n] = "1";             // a form leaves an unticked box out
            } else {
                data[f.n] = val === true ? "1" : val === false ? "0" : String(val);
            }
        });
        return data;
    };

    KnomiDevice.prototype.post = function (sec, path, data, status, json) {
        var self = this, body, ctype;
        if (json) {
            body = JSON.stringify(data);
            ctype = "application/json";
        } else {
            var p = new URLSearchParams();
            Object.keys(data).forEach(function (k) { p.append(k, data[k]); });
            p.append("quiet", "1");
            body = p.toString();
            ctype = "application/x-www-form-urlencoded";
        }
        status.className = "muted";
        status.textContent = "Saving…";
        return req("POST", path, body, ctype).then(answer).then(function (msg) {
            status.className = "text-success";
            status.textContent = msg;
            setTimeout(function () { self.refreshQuiet(); }, /Scanning/.test(msg) ? 6000 : 1500);
        }).catch(function (e) {
            status.className = "text-error";
            status.textContent = e.message;
        });
    };

    // re-read after a save, keeping the page in place (and the last message) if the KNOMI is busy
    KnomiDevice.prototype.refreshQuiet = function () {
        var self = this, y = window.scrollY;
        req("GET", "settings.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
            if (s && s.sections) { self.schema = s; self.render(); window.scrollTo(0, y); }
        }).catch(function () {});
    };

    KnomiDevice.prototype.fileField = function (sec, f, id, status) {
        var self = this;
        var file = el("input", {type: "file", id: id, accept: f.accept, style: "max-width:220px"});
        var st = el("span", {"class": "muted", text: f.v || "", style: "font-size:12px"});
        function send(path, form) {
            st.className = "muted";
            st.textContent = form ? "Uploading… (over Bluetooth this takes a while)" : "Working…";
            var h = {"X-CSRF-Token": token()};
            path += (path.indexOf("?") >= 0 ? "&" : "?") + "quiet=1";
            return fetch(K + path, {method: "POST", body: form || "", headers: h, credentials: "same-origin"}).then(answer).then(function (msg) {
                st.className = "text-success";
                st.textContent = msg;
                setTimeout(function () { self.refreshQuiet(); }, 1500);
            }).catch(function (e) { st.className = "text-error"; st.textContent = e.message; });
        }
        var up = el("button", {"class": "btn btn-small", onclick: function () {
            if (!file.files.length) { st.textContent = "Pick a file first"; return; }
            if (f.confirm && !window.confirm(f.confirm)) return;
            var fd = new FormData();
            fd.append(f.n, file.files[0], file.files[0].name);
            send(f.post, fd);
        }}, [el("i", {"class": "fa fa-upload"}), " Upload"]);
        var kids = [el("span", {text: f.l, style: "font-weight:600"}), st,
                    el("div", {style: "display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:2px"}, [file, up,
                        f.del ? el("button", {"class": "btn btn-small", onclick: function () { send(f.del, null); }}, [f.del_l || "Remove"]) : null])];
        if (f.h) kids.push(el("span", {"class": "muted", text: f.h, style: "font-size:12px"}));
        return el("div", {style: "display:flex;flex-direction:column;min-width:260px;padding:6px 0;border-bottom:1px solid rgba(128,128,128,.2);width:100%"}, kids);
    };

    KnomiDevice.prototype.viewField = function (f) {
        var pre = el("pre", {style: "max-height:320px;overflow:auto;font-size:11px;white-space:pre-wrap;width:100%;margin:0"}, ["…"]);
        function load() {
            pre.textContent = "Loading…";
            req("GET", f.src).then(function (r) { return r.text(); }).then(function (t) {
                pre.textContent = t || "(empty)";
                pre.scrollTop = pre.scrollHeight;
            }).catch(function (e) { pre.textContent = "Couldn't read it: " + e.message; });
        }
        var shown = false;
        var btn = el("button", {"class": "btn btn-small", onclick: function () { if (!shown) { shown = true; pre.style.display = ""; } load(); }}, [el("i", {"class": "fa fa-file-text-o"}), " Show"]);
        pre.style.display = "none";
        return el("div", {style: "width:100%"}, [btn, pre]);
    };

    // firmware: the plugin downloads the release (or takes a .bin) and sends it to the KNOMI
    KnomiDevice.prototype.firmwareField = function (f) {
        var st = el("span", {"class": "muted", style: "font-size:12px"});
        var file = el("input", {type: "file", accept: ".bin", style: "max-width:220px"});
        var timer = null;
        function poll() {
            OctoPrint.simpleApiGet("knomi").done(function (r) {
                var u = r.fw_update || {};
                if (!u.state || u.state === "idle") return;
                st.className = u.state === "error" ? "text-error" : u.state === "done" ? "text-success" : "muted";
                st.textContent = u.msg + (u.pct !== undefined && u.state === "sending" ? " " + u.pct + "%" : "");
                if (u.state === "done" || u.state === "error") { clearInterval(timer); timer = null; }
            });
        }
        function started(p) {
            p.done(function () { if (!timer) timer = setInterval(poll, 1500); poll(); })
             .fail(function (x) { st.className = "text-error"; st.textContent = (x.responseJSON && x.responseJSON.error) || "Couldn't start"; });
        }
        var gh = el("button", {"class": "btn btn-small btn-primary", onclick: function () {
            if (!window.confirm("Install the latest KNOMI firmware from GitHub? The KNOMI restarts when it's done.")) return;
            started(OctoPrint.simpleApiCommand("knomi", "fw_install", {repo: f.repo, asset: f.asset}));
        }}, [el("i", {"class": "fa fa-cloud-download"}), " Install latest from GitHub"]);
        var upl = el("button", {"class": "btn btn-small", onclick: function () {
            if (!file.files.length) { st.textContent = "Pick a .bin first"; return; }
            var fd = new FormData();
            fd.append("firmware", file.files[0], file.files[0].name);
            started($.ajax({url: BASE + "fw_upload", type: "POST", data: fd, processData: false, contentType: false,
                            headers: {"X-CSRF-Token": token()}}));
        }}, [el("i", {"class": "fa fa-upload"}), " Install this .bin"]);
        setTimeout(poll, 100);
        return el("div", {style: "display:flex;flex-direction:column;gap:4px;width:100%"}, [
            el("span", {text: f.l, style: "font-weight:600"}),
            f.h ? el("span", {"class": "muted", text: f.h, style: "font-size:12px"}) : null,
            el("div", {style: "display:flex;gap:8px;flex-wrap:wrap;align-items:center"}, [gh, el("span", {"class": "muted", text: "or"}), file, upl]),
            st]);
    };

    window.KnomiDevice = KnomiDevice;
})();
