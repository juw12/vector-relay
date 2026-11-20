/* Vector Relay on the phone: shows what the AI chats on your PC said, and takes your next prompt to them.

   Everything travels as AES-GCM ciphertext through a relay that cannot read it. The key came from
   the QR code on the PC, after the # of this page's address, which a browser sends to no server.
   In local mode the PC itself is the relay and serves this page, for a phone on the same Wi-Fi.
   A second computer can be added with a 4-digit code it shows, instead of its QR code.

   The phone asks the PC a question (route "feed", "prompt", "event" or "image") by leaving it at
   the relay and waiting there for the answer. When the PC does not answer, the last sealed copy
   the relay keeps is shown instead, with what stands in the way written in the bar. */
(function () {
  "use strict";

  var APP_VERSION = "1.2.2";  // the newest entry in versions.json; a test keeps the two in step
  var KEY = "vrelay.link", PENDING = "vrelay.pending", HINT = "vrelay.hint", CHAT = "vrelay.chat";
  var HANG_MS = 10000;          // a request with no reply for this long is dropped
  var ANSWER_HOLD_S = 8;        // the relay holds the request for the answer this long
  var ANSWER_HANG_MS = 15000;
  var FEED_WAIT_MS = 9000;      // how long the PC gets to answer
  var PROMPT_WAIT_MS = 20000;   // longer than the PC accepts a prompt for (15 s)
  var PHOTO_WAIT_MS = 50000;    // ... and a prompt with a photo (45 s): it takes a while to send
  var PHOTO_HANG_MS = 40000;
  var PICTURE_WAIT_MS = 25000;
  var PHOTO_SIDE = 1600, PHOTO_CHARS = 600000;   // a photo is made this small before it is sent
  var BUSY_EVERY_MS = 2500, QUIET_EVERY_MS = 7000, BUSY_FOR_MS = 120000;
  var SILENT_S = 30 * 60;       // a chat that is on, not listening and silent this long has probably stopped
  var KEPT_EVENTS = 300;

  var $ = function (id) { return document.getElementById(id); };
  var store = (function () {
    try { localStorage.setItem("vrelay.test", "1"); localStorage.removeItem("vrelay.test"); return localStorage; }
    catch (error) {
      var memory = {};
      return { getItem: function (k) { return memory[k] || null; }, setItem: function (k, v) { memory[k] = v; }, removeItem: function (k) { delete memory[k]; } };
    }
  }());

  function fromBase64(text) {
    var plain = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
    var bytes = new Uint8Array(plain.length);
    for (var i = 0; i < plain.length; i += 1) bytes[i] = plain.charCodeAt(i);
    return bytes;
  }
  function toBase64(bytes) {
    var plain = "";
    for (var i = 0; i < bytes.length; i += 0x8000) plain += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(plain).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function randomId() { return toBase64(crypto.getRandomValues(new Uint8Array(12))); }
  function pause(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  // ------------------------------------------------------------ the code from the PC
  /* https anywhere; http only for the PC itself and its own network (a .local name or a private address). */
  var NEAR = /^(https:\/\/[^\/\s]+|http:\/\/(127\.0\.0\.1|localhost|[A-Za-z0-9-]+\.local|10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2})(:\d+)?)$/;
  function good(found) {
    if (!found || typeof found.u !== "string" || !NEAR.test(found.u) ||
        !/^[A-Za-z0-9_-]{16,64}$/.test(found.c || "") || !/^[A-Za-z0-9_-]{43}$/.test(found.k || "")) return null;
    try {
      var origin = new URL(found.u);
      if (origin.origin !== found.u || fromBase64(found.k).length !== 32) return null;
    } catch (error) { return null; }
    return { u: found.u, c: found.c, k: found.k };
  }
  var ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var standalone = navigator.standalone === true || window.matchMedia("(display-mode: standalone)").matches;

  var DEVICES = 'vrelay.devices.v1', ACTIVE = 'vrelay.device.active';
  function readStore(key, fallback) {
    try { return JSON.parse(store.getItem(key)) || fallback; }
    catch (error) { return fallback; }
  }
  function decodeTicket(hash) {
    var match = /^#r=([A-Za-z0-9_-]{1,4096})$/.exec(hash);
    if (!match) return null;
    try { return good(JSON.parse(new TextDecoder().decode(fromBase64(match[1])))); }
    catch (error) { return null; }
  }
  function idOf(ticket) { return ticket.u + '/c/' + ticket.c; }
  function ticketHash(ticket) {
    return '#r=' + toBase64(new TextEncoder().encode(JSON.stringify(ticket)));
  }
  var devices = readStore(DEVICES, []);
  devices = Array.isArray(devices) ? devices.filter(function (d) { return d && good(d.link) && typeof d.name === 'string'; }) : [];
  function deviceById(id) { return devices.find(function (d) { return idOf(d.link) === id; }); }
  function keepDevices() { store.setItem(DEVICES, JSON.stringify(devices)); }
  function remember(ticket, alias) {
    var entry = deviceById(idOf(ticket));
    if (entry && entry.link.k !== ticket.k) return null;
    if (!entry) {
      entry = { link: ticket, name: 'Computer ' + (devices.length + 1), named: false };
      devices.push(entry);
    }
    if (alias) { entry.name = alias; entry.named = true; }
    keepDevices();
    return entry;
  }
  var legacy = good(readStore(KEY, null));
  if (legacy) {
    remember(legacy);
    [CHAT, PENDING].forEach(function (key) {
      var old = store.getItem(key), scoped = key + ':' + idOf(legacy);
      if (old !== null && store.getItem(scoped) === null) store.setItem(scoped, old);
      store.removeItem(key);
    });
    store.removeItem(KEY);
  }
  var incoming = decodeTicket(location.hash);
  var active = deviceById(store.getItem(ACTIVE));
  /* An installed iPhone app may reopen the URL used to create its icon. Its original
     computer is only a bootstrap pairing, not a reason to undo an explicit device switch. */
  if (incoming && !(standalone && active && deviceById(idOf(incoming)))) active = remember(incoming);
  if (!active) active = devices[0] || null;
  var link = active ? active.link : null;
  if (active) {
    store.setItem(ACTIVE, idOf(link));
    CHAT += ':' + idOf(link);
    PENDING += ':' + idOf(link);
    history.replaceState(null, '', location.pathname + location.search + (ios ? ticketHash(link) : ''));
  }

  // ------------------------------------------------------------ sealed questions and answers
  function trouble(kind, status) {
    var error = new Error(kind);
    error.kind = kind;
    error.status = status || 0;
    return error;
  }

  /* fetch that gives up: a phone that wakes can hand a page a connection that is already dead. */
  function reach(url, options, ms) {
    var settings = options || {};
    var stop = typeof AbortController === "function" ? new AbortController() : null;
    var timer = stop ? setTimeout(function () { stop.abort(); }, ms) : 0;
    if (stop) settings.signal = stop.signal;
    return fetch(url, settings).then(function (reply) { clearTimeout(timer); return reply; },
      function () { clearTimeout(timer); throw trouble("unreachable"); });
  }

  var local = !!link && link.u.indexOf("http://") === 0;   // the PC serves this page itself, over Wi-Fi
  /* A browser lends its own AES only to pages that came over https. A page from the PC on the
     Wi-Fi came over http, so there the sealing is done by softcrypto.js: same key, same layout. */
  var subtle = window.crypto && window.crypto.subtle ? window.crypto.subtle : null;
  var keyPromise = null, soft = null;
  if (link) {
    try {
      if (subtle) keyPromise = subtle.importKey("raw", fromBase64(link.k), "AES-GCM", false, ["encrypt", "decrypt"]);
      else soft = window.softGcm(fromBase64(link.k));
    } catch (error) { link = null; }   // a code with a broken key: the page says it is not linked
  }

  function seal(data) {
    var iv = crypto.getRandomValues(new Uint8Array(12));
    var plain = new TextEncoder().encode(JSON.stringify(data));
    var made = soft
      ? new Promise(function (resolve) { resolve(soft.seal(iv, plain)); })
      : keyPromise.then(function (key) { return subtle.encrypt({ name: "AES-GCM", iv: iv }, key, plain); })
        .then(function (sealed) { return new Uint8Array(sealed); });
    return made.then(function (sealed) {
      var all = new Uint8Array(12 + sealed.length);
      all.set(iv, 0);
      all.set(sealed, 12);
      return toBase64(all);
    });
  }
  function unseal(text) {
    var raw = fromBase64(text.trim());
    var opened = soft
      ? new Promise(function (resolve) { resolve(soft.open(raw.subarray(0, 12), raw.subarray(12))); })
      : keyPromise.then(function (key) { return subtle.decrypt({ name: "AES-GCM", iv: raw.slice(0, 12) }, key, raw.slice(12)); });
    return opened.then(function (plain) { return JSON.parse(new TextDecoder().decode(plain)); },
      function () { throw trouble("unreadable"); });
  }

  var base = link ? link.u + "/c/" + link.c : "";

  /* Leaves a sealed question for the PC and waits for the answer until waitMs have passed.
     The relay holds each request for the answer a few seconds, so one request is usually enough. */
  async function ask(route, more, waitMs, hangMs) {
    var id = randomId();
    var question = Object.assign({ route: route, t: Date.now() + clockOffset }, more || {});
    var reply = await reach(base + "/ask/" + id, { method: "POST", headers: { "Content-Type": "text/plain" }, body: await seal(question) }, hangMs || HANG_MS);
    if (reply.status === 429) throw trouble("silent");
    if (!reply.ok) throw trouble("refused", reply.status);
    var started = Date.now(), wait = 250;
    while (Date.now() - started < waitMs) {
      var asked = Date.now();
      reply = await reach(base + "/answer/" + id + "?wait=" + ANSWER_HOLD_S, { cache: "no-store" }, ANSWER_HANG_MS);
      if (reply.status === 200) return unseal(await reply.text());
      if (reply.status !== 204) throw trouble("refused", reply.status);
      if (Date.now() - asked < 1000) {   // this relay did not hold the request: look again in a moment
        await pause(wait);
        wait = Math.min(wait * 1.4, 1000);
      }
    }
    throw trouble("silent");
  }

  // ------------------------------------------------------------ what this phone knows
  var events = [];          // the feed of every chat, oldest first
  var lastN = 0;            // the newest event number a live answer brought
  var waiting = [];         // prompt ids no chat has picked up yet
  var info = { chats: [], pc: 0 };
  var selected = store.getItem(CHAT) || "";   // the chat this page shows and sends to
  var unread = {};          // chat id -> something new came while another chat was shown
  var problem = "";         // what stands between this phone and the PC, in plain words
  var pending = [];         // prompts typed here that the feed does not show yet
  var photo = null;         // the photo about to be sent: {data, w, h, url}
  var mine = {};            // prompt id -> the photo this page sent with it, so it is not fetched back
  var pictures = {};        // event number -> {state: "loading" | "ready" | "failed", url}
  var loading = false;
  var busyUntil = 0, timer = 0, asking = false, shown = "";
  var clockOffset = 0;      // PC clock minus this phone's, in ms: questions are stamped in PC time
  try { pending = JSON.parse(store.getItem(PENDING) || "[]").filter(function (p) { return p && p.pid && p.text; }); } catch (error) { pending = []; }
  pending.forEach(function (p) {
    if (p.state === "sending") { p.state = "failed"; p.why = "Not sent."; }
    if (p.photo) p.why = "Not sent, and the photo is gone from this page. Add it again.";
  });

  function savePending() {
    store.setItem(PENDING, JSON.stringify(pending.map(function (p) {
      return { pid: p.pid, text: p.text, t: p.t, chat: p.chat, state: p.state, why: p.why, photo: p.photo };
    })));
  }

  function chat(id) { return info.chats.filter(function (c) { return c.id === id; })[0] || null; }
  function chatName(c) { return c.label || "Chat " + c.id; }

  /* Keeps the chosen chat while the PC still knows it; else takes one that is in phone mode. */
  function choose() {
    if (chat(selected)) return;
    var on = info.chats.filter(function (c) { return c.on; })[0];
    selected = (on || info.chats[0] || { id: "" }).id;
    if (selected) store.setItem(CHAT, selected);
  }

  function select(id) {
    selected = id;
    store.setItem(CHAT, id);
    delete unread[id];
    shown = "";
    render();
    $("feed").scrollTop = $("feed").scrollHeight;
    again();
  }

  function here(event) { return !event.chat || !selected || event.chat === selected; }

  function merge(answer, live) {
    if (!answer || !Array.isArray(answer.events)) return;
    if (live && active && !active.named && typeof answer.deviceName === 'string' && answer.deviceName.trim()) {
      var reported = answer.deviceName.trim().slice(0, 40);
      if (active.name !== reported) {
        active.name = reported;
        keepDevices();
        deviceLabels();
      }
    }
    if (live && answer.last < lastN) { events = []; lastN = 0; }   // the feed on the PC was cleared
    var before = events.length ? events[events.length - 1].n : 0;
    var byNumber = {};
    events.forEach(function (event) { byNumber[event.n] = event; });
    answer.events.forEach(function (event) {
      var old = byNumber[event.n];
      if (!live && old && !old.cut) return;   // the relay's copy is shorter than what is here
      byNumber[event.n] = event;
    });
    events = Object.keys(byNumber).map(function (n) { return byNumber[n]; }).sort(function (a, b) { return a.n - b.n; }).slice(-KEPT_EVENTS);
    if (live) lastN = Math.max(lastN, answer.last || 0);
    if (live && answer.pc) clockOffset = answer.pc * 1000 - Date.now();
    info = { chats: Array.isArray(answer.chats) ? answer.chats : info.chats, pc: answer.pc || info.pc };
    waiting = answer.waiting || [];
    choose();
    var ids = {};
    events.forEach(function (event) {
      if (event.id) ids[event.id] = true;
      if (before && event.n > before && event.chat && event.chat !== selected && event.from !== "phone") unread[event.chat] = true;
    });
    var left = pending.filter(function (p) { return !ids[p.pid]; });
    if (left.length !== pending.length) { pending = left; savePending(); }
    if (events.length && events[events.length - 1].n > before) busyUntil = Date.now() + BUSY_FOR_MS;
  }

  // ------------------------------------------------------------ words on the page
  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function inline(text) {
    return escapeHtml(text)
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }
  function table(rows) {
    var cells = rows.map(function (row) { return row.trim().replace(/^\||\|$/g, "").split("|").map(function (cell) { return cell.trim(); }); });
    var ruled = cells.length > 1 && cells[1].every(function (cell) { return /^:?-{2,}:?$/.test(cell); });
    var html = "<div class=\"scroll\"><table>";
    cells.forEach(function (row, index) {
      if (ruled && index === 1) return;
      var tag = ruled && index === 0 ? "th" : "td";
      html += "<tr>" + row.map(function (cell) { return "<" + tag + ">" + inline(cell) + "</" + tag + ">"; }).join("") + "</tr>";
    });
    return html + "</table></div>";
  }
  var LIST = /^\s*([-*]|\d+[.)])\s+(.*)$/;
  /* The small part of Markdown a chat's answers use: headings, lists, tables, code, bold, links. */
  function markdown(text) {
    var lines = text.replace(/\r/g, "").split("\n"), html = [], paragraph = [], i = 0, found;
    function flush() {
      if (paragraph.length) html.push("<p>" + paragraph.map(inline).join("<br>") + "</p>");
      paragraph = [];
    }
    while (i < lines.length) {
      var line = lines[i];
      if (/^\s*```/.test(line)) {
        flush();
        var code = [];
        for (i += 1; i < lines.length && !/^\s*```/.test(lines[i]); i += 1) code.push(lines[i]);
        i += 1;
        html.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>");
      } else if (/^\s*\|.*\|\s*$/.test(line)) {
        flush();
        var rows = [];
        for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i += 1) rows.push(lines[i]);
        html.push(table(rows));
      } else if ((found = /^#{1,6}\s+(.*)$/.exec(line))) {
        flush();
        html.push("<h3>" + inline(found[1]) + "</h3>");
        i += 1;
      } else if ((found = LIST.exec(line))) {
        flush();
        var tag = /\d/.test(found[1]) ? "ol" : "ul", items = [];
        for (; i < lines.length && (found = LIST.exec(lines[i])); i += 1) items.push("<li>" + inline(found[2]) + "</li>");
        html.push("<" + tag + ">" + items.join("") + "</" + tag + ">");
      } else if (!line.trim()) {
        flush();
        i += 1;
      } else {
        paragraph.push(line);
        i += 1;
      }
    }
    flush();
    return html.join("");
  }

  function two(number) { return (number < 10 ? "0" : "") + number; }
  function clock(seconds) { var d = new Date(seconds * 1000); return two(d.getHours()) + ":" + two(d.getMinutes()); }
  function day(seconds) {
    var d = new Date(seconds * 1000), today = new Date();
    if (d.toDateString() === today.toDateString()) return "Today";
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }
  function ago(seconds) {
    var minutes = Math.max(0, Math.round((Date.now() / 1000 - seconds) / 60));
    if (minutes < 1) return "just now";
    if (minutes < 60) return minutes + " min ago";
    return "at " + clock(seconds);
  }

  function element(tag, name, text) {
    var made = document.createElement(tag);
    if (name) made.className = name;
    if (text !== undefined) made.textContent = text;
    return made;
  }

  function visible() { return events.filter(here); }

  /* [dot, words, may a prompt be sent] for the chat this page shows. */
  function status() {
    var own = chat(selected), seen = visible();
    var last = seen.length ? seen[seen.length - 1] : null;
    if (problem) return ["bad", problem, !!own && own.on];
    if (!info.pc) return ["", "Asking your PC", false];
    if (!own) return ["", active ? "No chat on " + active.name + " is in phone mode. On that computer, type /phone in a chat." : "No chat is in phone mode. On the PC, type /phone in a chat.", false];
    if (!own.on) {
      var others = info.chats.some(function (c) { return c.on; });
      var nobody = active ? " No chat on " + active.name + " is in phone mode now: on that computer, type /phone in a chat." : " On the PC, type /phone in a chat.";
      return ["", "This chat left phone mode." + (others ? " Pick another one from the chat list." : nobody), false];
    }
    if (own.listening) return ["ok", "Vector is waiting for your prompt", true];
    if (last && Date.now() / 1000 - last.t > SILENT_S) return ["bad", "No word from the chat since " + clock(last.t) + ". It may have stopped: look at the PC.", true];
    return ["busy", "Vector is working" + (last ? " · last word " + ago(last.t) : ""), true];
  }

  function picture(event) {
    var size = event.img || {}, figure = element("figure", "pic");
    var kept = event.id && mine[event.id] ? { state: "ready", url: mine[event.id] } : event.local ? { state: "ready", url: event.local } : pictures[event.n];
    if (kept && kept.state === "ready") {
      var image = element("img");
      image.alt = "Picture";
      if (size.w && size.h) { image.width = size.w; image.height = size.h; }
      image.src = kept.url;
      figure.appendChild(image);
      return figure;
    }
    var wait = element(kept && kept.state === "failed" ? "button" : "div", "wait", kept && kept.state === "failed" ? "The picture did not come. Try again" : "Getting the picture");
    if (size.w && size.h) {
      wait.style.width = size.w + "px";
      wait.style.aspectRatio = size.w + " / " + size.h;
    } else wait.style.height = "160px";
    if (kept && kept.state === "failed") {
      wait.type = "button";
      wait.onclick = function () { delete pictures[event.n]; shown = ""; render(); };
    }
    figure.appendChild(wait);
    return figure;
  }

  function bubble(event) {
    var own = event.kind === "prompt";
    var item = element("article", "item " + event.kind + (event.state === "failed" ? " failed" : ""));
    var text = element("div", "text");
    if (event.img || event.local) text.appendChild(picture(event));
    var words = element("div", "words");
    if (event.kind === "result" || event.kind === "error") words.innerHTML = markdown(event.text);
    else if (own) words.textContent = event.text;
    else words.innerHTML = inline(event.text);
    if (event.text && !((event.img || event.local) && event.text === "(photo)")) text.appendChild(words);
    item.appendChild(text);
    if (event.cut) {
      var more = element("button", "more", "Show all");
      more.type = "button";
      more.onclick = function () { showAll(event, more); };
      text.appendChild(more);
    }
    var meta = element("div", "meta", clock(event.t));
    if (own && event.state === "sending") meta.textContent += " · sending";
    else if (own && event.state === "failed") {
      meta.textContent = (event.why || "Not sent.") + " ";
      if (!event.photo || event.local) {
        var again = element("button", "retry", "Try again");
        again.type = "button";
        again.onclick = function () { deliver(event); };
        meta.appendChild(again);
        meta.appendChild(document.createTextNode(" "));
      }
      var drop = element("button", "drop", "Remove");
      drop.type = "button";
      drop.onclick = function () {
        pending = pending.filter(function (p) { return p.pid !== event.pid; });
        savePending();
        render();
      };
      meta.appendChild(drop);
    } else if (own && event.from === "phone") {
      meta.textContent += waiting.indexOf(event.id) >= 0 ? " · waiting for the chat" : " · picked up";
    } else if (own) meta.textContent += " · typed on the PC";
    item.appendChild(meta);
    return item;
  }

  /* "now", "4m", "2h", "3d": how long ago, the way the chat list in the app says it. */
  function since(seconds) {
    var passed = Math.max(0, Date.now() / 1000 - seconds);
    if (passed < 60) return "now";
    if (passed < 3600) return Math.floor(passed / 60) + "m";
    if (passed < 86400) return Math.floor(passed / 3600) + "h";
    return Math.floor(passed / 86400) + "d";
  }

  /* When each chat last said or heard something. */
  function lastWord(c) {
    var last = c.since || 0;
    events.forEach(function (event) { if (event.chat === c.id && event.t > last) last = event.t; });
    return last;
  }

  var listed = "";
  function chips() {
    var bar = $("chats"), wanted = $("find").value.trim().toLowerCase();
    var rows = info.chats.map(function (c) {
      return { c: c, name: chatName(c), when: since(lastWord(c)), last: lastWord(c) };
    }).sort(function (a, b) { return b.last - a.last; });
    var found = rows.filter(function (row) { return !wanted || row.name.toLowerCase().indexOf(wanted) >= 0; });
    var some = Object.keys(unread).some(function (id) { return unread[id] && id !== selected; });
    $("menuNew").hidden = !some;
    $("noChats").hidden = found.length > 0;
    $("noChats").textContent = rows.length ? "No chat with that name." : "No chat is in phone mode yet. On the PC, type /phone in a chat.";
    var mark = JSON.stringify([selected, unread, found.map(function (row) { return [row.c.id, row.name, row.when, row.c.on, row.c.listening]; })]);
    if (mark === listed) return;   // rows are not made again under a finger
    listed = mark;
    bar.textContent = "";
    found.forEach(function (row) {
      var c = row.c;
      var chip = element("button", "chip" + (c.id === selected ? " chosen" : "") + (c.on ? "" : " away"));
      chip.type = "button";
      chip.setAttribute("aria-pressed", c.id === selected ? "true" : "false");
      chip.appendChild(element("span", "name", row.name));
      if (c.on && !c.listening) {
        var spin = element("span", "spin");
        spin.title = "Working";
        chip.appendChild(spin);
      }
      if (unread[c.id]) {
        var fresh = element("span", "new");
        fresh.title = "Something new";
        chip.appendChild(fresh);
      }
      chip.appendChild(element("span", "when", row.when));
      chip.onclick = function () { select(c.id); side(false); };
      bar.appendChild(chip);
    });
  }

  /* The chat list slides in over the feed on a phone; on a wide screen it is always there. */
  function side(open) {
    document.body.classList.toggle("side-open", open);
    $("shade").hidden = !open;
    $("menu").setAttribute("aria-expanded", open ? "true" : "false");
    if (open) chips();
  }

  function render() {
    var found = status(), own = chat(selected);
    var all = visible().concat(pending.filter(here).map(function (p) {
      return { kind: "prompt", from: "phone", text: p.text, t: p.t, state: p.state, why: p.why, pid: p.pid, id: p.pid, chat: p.chat, photo: p.photo, local: p.img ? p.url : "" };
    }));
    var mark = JSON.stringify([found, selected, info.chats, unread, waiting, all.map(function (e) {
      return [e.n || e.pid, e.state || "", e.cut ? 1 : 0, e.text.length, e.n && pictures[e.n] ? pictures[e.n].state : ""];
    })]);
    $("dot").className = "dot " + found[0];
    $("status").textContent = found[1];
    $("title").textContent = own ? chatName(own) : "Vector Relay";
    document.title = own ? chatName(own) : "Vector Relay";
    $("send").disabled = !found[2];
    $("box").placeholder = found[2] ? "Send follow-up" : "No chat to send to";
    chips();
    if (mark === shown) return;
    shown = mark;
    var feed = $("feed");
    var atEnd = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
    feed.textContent = "";
    if (!all.length) feed.appendChild(element("p", "empty", info.pc ? "Nothing yet. What the chat says will show here." : ""));
    var lastDay = "";
    all.forEach(function (event) {
      var label = day(event.t);
      if (label !== lastDay) { feed.appendChild(element("p", "day", label)); lastDay = label; }
      feed.appendChild(bubble(event));
    });
    if (atEnd) feed.scrollTop = feed.scrollHeight;
    getPictures();
  }

  /* Pictures come one at a time, newest first, each as its own sealed answer. */
  async function getPictures() {
    if (loading || problem) return;
    var next = visible().filter(function (event) { return event.img && !pictures[event.n] && !(event.id && mine[event.id]); }).pop();
    if (!next) return;
    loading = true;
    pictures[next.n] = { state: "loading" };
    try {
      var answer = await ask("image", { n: next.n }, PICTURE_WAIT_MS);
      if (!answer.ok || !answer.data) throw trouble("gone");
      var plain = atob(answer.data), bytes = new Uint8Array(plain.length);
      for (var i = 0; i < plain.length; i += 1) bytes[i] = plain.charCodeAt(i);
      pictures[next.n] = { state: "ready", url: URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })) };
    } catch (error) {
      pictures[next.n] = { state: "failed" };
    }
    loading = false;
    render();
    getPictures();
  }

  async function showAll(event, button) {
    button.disabled = true;
    button.textContent = "Getting it";
    try {
      var answer = await ask("event", { n: event.n }, FEED_WAIT_MS);
      if (answer.ok && answer.event) {
        events = events.map(function (old) { return old.n === event.n ? answer.event : old; });
        render();
        return;
      }
    } catch (error) { /* said below */ }
    button.disabled = false;
    button.textContent = "Could not get it. Try again";
  }

  // ------------------------------------------------------------ talking to the PC
  /* Why the PC did not answer: looks at the copy the relay keeps, which also fills the page. */
  async function explain(error) {
    if (navigator.onLine === false) return "This phone is offline.";
    if (error.kind === "unreadable") return "The key on this phone does not open what the PC sends. Scan a new code on the PC.";
    var reply;
    try { reply = await reach(base + "/state", { cache: "no-store" }, HANG_MS); }
    catch (failure) {
      if (local) return "This phone cannot reach your PC. Is the PC on and awake, with a chat in phone mode, and is this phone on the same Wi-Fi?";
      return "This phone cannot reach the relay. Check its internet, or switch between Wi-Fi and mobile data.";
    }
    if (reply.status === 404 && local) return "Your PC does not know this phone's code any more. Make a new code on the PC and scan it.";
    if (reply.status === 404) return "The relay has nothing from this computer yet. On the computer, type /phone in a chat. If that does not help, add it again: type /phone code there.";
    if (!reply.ok) return "The relay answered with an error (" + reply.status + "). Try again in a minute.";
    try { merge(await unseal(await reply.text()), false); }
    catch (failure) { return "The key on this phone does not open what the PC sends. Scan a new code on the PC."; }
    if (error.kind === "refused") return "The relay answered with an error (" + error.status + "). Try again in a minute.";
    if (!info.chats.some(function (c) { return c.on; })) return "";   // the computer itself said no chat is in phone mode: nothing is broken
    return "Your PC does not answer" + (info.pc ? ". It was last heard from " + ago(info.pc) + "." : ".") + " Is it on, and is a chat in phone mode?";
  }

  async function look() {
    if (asking || !link) return;
    asking = true;
    try {
      var answer = await ask("feed", { after: lastN }, FEED_WAIT_MS);
      if (answer.ok) { problem = ""; merge(answer, true); }
      else problem = "The key on this phone does not open what the PC sends. Scan a new code on the PC.";
    } catch (error) {
      problem = await explain(error);
    }
    asking = false;
    render();
    again();
  }

  function again() {
    clearTimeout(timer);
    if (document.hidden || !link) return;
    var own = chat(selected);
    var busy = Date.now() < busyUntil || pending.some(function (p) { return p.state === "sending"; }) || (own && own.on && !own.listening && !problem);
    timer = setTimeout(look, busy ? BUSY_EVERY_MS : QUIET_EVERY_MS);
  }

  var WHY = {
    old: "It reached the PC too late, so the PC did not take it.",
    bad: "The PC refused it: empty or too long.",
    key: "The key on this phone is not the PC's. Scan a new code.",
    off: "That chat is not in phone mode, so nothing would pick it up.",
    chat: "Several chats are in phone mode. Pick one from the chat list, then try again.",
    photo: "The PC could not read the photo."
  };

  async function deliver(prompt) {
    var own = pending.filter(function (p) { return p.pid === prompt.pid; })[0];
    if (!own) return;
    own.state = "sending";
    own.why = "";
    savePending();
    render();
    busyUntil = Date.now() + BUSY_FOR_MS;
    var question = { pid: own.pid, text: own.text, chat: own.chat || selected, after: lastN };
    if (own.img) { question.img = own.img; question.w = own.w; question.h = own.h; mine[own.pid] = own.url; }
    try {
      var answer = await ask("prompt", question, own.img ? PHOTO_WAIT_MS : PROMPT_WAIT_MS, own.img ? PHOTO_HANG_MS : HANG_MS);
      if (answer.ok) { problem = ""; merge(answer, true); }
      else { own.state = "failed"; own.why = WHY[answer.error] || "The PC refused it."; }
    } catch (error) {
      own.state = "failed";
      own.why = error.kind === "silent" ? "Your PC did not answer." : "Not sent.";
    }
    savePending();
    render();
    again();
  }

  // ------------------------------------------------------------ a photo to send along
  function load(file) {
    return new Promise(function (resolve, reject) {
      var address = URL.createObjectURL(file), image = new Image();
      image.onload = function () { URL.revokeObjectURL(address); resolve(image); };
      image.onerror = function () { URL.revokeObjectURL(address); reject(new Error("not a picture")); };
      image.src = address;
    });
  }

  /* The photo as a JPEG small enough to travel sealed through the relay. */
  async function shrink(file) {
    var image = await load(file);
    var side = PHOTO_SIDE, quality = 0.82, canvas = document.createElement("canvas"), url = "";
    for (var attempt = 0; attempt < 8; attempt += 1) {
      var scale = Math.min(1, side / Math.max(image.naturalWidth, image.naturalHeight));
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      var pen = canvas.getContext("2d");
      pen.fillStyle = "#fff";
      pen.fillRect(0, 0, canvas.width, canvas.height);
      pen.drawImage(image, 0, 0, canvas.width, canvas.height);
      url = canvas.toDataURL("image/jpeg", quality);
      if (url.length - 23 <= PHOTO_CHARS) break;
      if (quality > 0.6) quality -= 0.12; else side = Math.round(side * 0.8);
    }
    var data = url.slice(url.indexOf(",") + 1);
    if (url.indexOf("data:image/jpeg") !== 0 || data.length > PHOTO_CHARS) throw new Error("too large");
    return { data: data, w: canvas.width, h: canvas.height, url: url };
  }

  function setPhoto(made) {
    photo = made;
    $("preview").hidden = !made;
    if (made) $("previewPicture").src = made.url; else $("previewPicture").removeAttribute("src");
    $("photo").value = "";
  }

  // ------------------------------------------------------------ a 4-digit code instead of the QR code
  /* The computer shows 4 digits. It and this phone each turn them into a number, raise it to a
     secret of their own and swap the results (SPEKE, in the 2048-bit group of RFC 3526). Only two
     sides that started from the same digits end with the same key, and what crosses the relay says
     nothing about the digits: a guess has to be sent to the computer, which takes three. The
     pairing link then arrives sealed with the key the two just made.
     The swap happens in a "room": a channel of the relay named by the time alone. */
  var CODE_PRIME =
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD" +
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED" +
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F" +
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B" +
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510" +
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF";
  var ROOM_EVERY_MS = 5 * 60 * 1000, ROOM_HANG_MS = 6000, CODE_WAIT_MS = 25000;
  var canCode = !!subtle && typeof BigInt === "function";   // not on a page that came over plain http from the PC

  function bytesOf(text) { return new TextEncoder().encode(text); }
  function joined(parts) {
    var all = new Uint8Array(parts.reduce(function (sum, part) { return sum + part.length; }, 0)), at = 0;
    parts.forEach(function (part) { all.set(part, at); at += part.length; });
    return all;
  }
  function digest(bytes) {
    return subtle.digest("SHA-256", bytes).then(function (made) { return new Uint8Array(made); });
  }
  function signed(key, text) {
    return subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"])
      .then(function (made) { return subtle.sign("HMAC", made, bytesOf(text)); })
      .then(function (made) { return new Uint8Array(made); });
  }
  function numberOf(bytes) {
    var hex = "0";
    for (var i = 0; i < bytes.length; i += 1) hex += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
    return BigInt("0x" + hex);
  }
  function wide(number) {   // 256 bytes, the highest first
    var hex = number.toString(16), bytes = new Uint8Array(256);
    hex = new Array(513 - hex.length).join("0") + hex;
    for (var i = 0; i < 256; i += 1) bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
    return bytes;
  }
  function power(base, exponent, modulus) {
    var result = BigInt(1), none = BigInt(0), one = BigInt(1);
    base %= modulus;
    while (exponent > none) {
      if (exponent & one) result = result * base % modulus;
      base = base * base % modulus;
      exponent >>= one;
    }
    return result;
  }
  async function codeBase(code, prime) {
    var parts = [];
    for (var count = 0; count < 10; count += 1) {
      parts.push(await digest(joined([bytesOf("vector-relay pair v1|" + code), new Uint8Array([count])])));
    }
    var number = numberOf(joined(parts)) % prime;
    return number * number % prime;
  }

  /* The room a computer holds at this address for this stretch of time, or null. */
  async function lookRoom(origin, stretch) {
    var named = await digest(bytesOf("vector-relay pair room v1|" + stretch));
    var room = origin + "/c/pair" + toBase64(named).slice(0, 28);
    var reply = await reach(room + "/state", { cache: "no-store" }, ROOM_HANG_MS);
    if (reply.status !== 200) return null;
    try {
      var found = JSON.parse(await reply.text());
      return found.v === 1 && typeof found.a === "string" && typeof found.t === "number" ? { base: room, a: found.a, t: found.t } : null;
    } catch (error) { return null; }
  }

  /* The pairing link of the computer that shows this code. Throws trouble("no-code", "wrong", "silent" or "unreachable"). */
  async function linkByCode(code) {
    var origins = [location.origin], stretch = Math.floor(Date.now() / ROOM_EVERY_MS), looks = [];
    if (link && link.u !== location.origin) origins.push(link.u);   // a page that is not at its relay's address
    origins.forEach(function (origin) {   // a code lives 5 minutes, and the two clocks may differ by a few more
      for (var step = -2; step <= 1; step += 1) looks.push(lookRoom(origin, stretch + step).catch(function () { return false; }));
    });
    var found = await Promise.all(looks);
    var rooms = found.filter(Boolean).sort(function (a, b) { return b.t - a.t; });
    if (!rooms.length) throw trouble(found.every(function (room) { return room === false; }) ? "unreachable" : "no-code");
    var room = rooms[0], prime = BigInt("0x" + CODE_PRIME), one = BigInt(1);
    var theirs = numberOf(fromBase64(room.a));
    if (theirs <= one || theirs >= prime - one) throw trouble("no-code");
    var secret = numberOf(crypto.getRandomValues(new Uint8Array(32))) + BigInt(2);
    var mine = power(await codeBase(code, prime), secret, prime);
    var shared = power(theirs * theirs % prime, secret, prime);
    var key = await digest(joined([bytesOf("vector-relay pair v1 key|"), wide(theirs), wide(mine), wide(shared)]));
    var proof = await signed(key, "proof"), sealKey = await signed(key, "seal");
    var id = randomId();
    var reply = await reach(room.base + "/ask/" + id, {
      method: "POST", headers: { "Content-Type": "text/plain" }, body: JSON.stringify({ b: toBase64(wide(mine)), m: toBase64(proof) }),
    }, HANG_MS);
    if (!reply.ok) throw trouble(reply.status === 404 ? "no-code" : "silent");
    var started = Date.now();
    while (Date.now() - started < CODE_WAIT_MS) {
      var asked = Date.now();
      reply = await reach(room.base + "/answer/" + id + "?wait=" + ANSWER_HOLD_S, { cache: "no-store" }, ANSWER_HANG_MS);
      if (reply.status === 200) {
        var answer = JSON.parse(await reply.text());
        if (!answer.ok) {
          var wrong = trouble("wrong");
          wrong.left = answer.left;
          throw wrong;
        }
        var raw = fromBase64(answer.link);
        var opener = await subtle.importKey("raw", sealKey, "AES-GCM", false, ["decrypt"]);
        var plain = await subtle.decrypt({ name: "AES-GCM", iv: raw.slice(0, 12) }, opener, raw.slice(12));
        return JSON.parse(new TextDecoder().decode(plain)).link;
      }
      if (reply.status !== 204) throw trouble("silent");
      if (Date.now() - asked < 1000) await pause(400);   // this relay did not hold the request
    }
    throw trouble("silent");
  }
  function codeWhy(error) {
    if (navigator.onLine === false || error.kind === "unreachable") return "This phone cannot reach the relay. Check its internet, then try again.";
    if (error.kind === "no-code") return "No computer is showing a code right now. On that computer, type /phone code in a chat, then enter its 4 digits here.";
    if (error.kind === "wrong" && error.left > 0) return "That is not the code. Check the 4 digits on the computer and try again.";
    if (error.kind === "wrong") return "That is not the code, and it has stopped working now. Make a new code on the computer.";
    return "The computer did not answer. Make a new code on it and try again.";
  }

  // ------------------------------------------------------------ start
  function deviceLabels() {
    if (!active) return;
    var picker = $('devicePicker');
    picker.replaceChildren();
    devices.forEach(function (d) {
      var option = element('option', '', d.name);
      option.value = idOf(d.link);
      picker.appendChild(option);
    });
    picker.value = idOf(active.link);
    $('deviceControls').hidden = false;
    $('sendTarget').textContent = 'To: ' + active.name;
    $('send').setAttribute('aria-label', 'Send to ' + active.name);
  }
  function switchDevice(next) {
    if (!next) return;
    if (active && idOf(next.link) === idOf(active.link)) { deviceLabels(); return; }
    if (active && pending.some(function (p) { return p.state === 'sending'; })) {
      window.alert('A prompt is still sending. Switch devices after it finishes.');
      deviceLabels();
      return;
    }
    if (active && ($('box').value.trim() || photo) && !window.confirm('Discard the unsent draft and switch devices?')) {
      deviceLabels();
      return;
    }
    store.setItem(ACTIVE, idOf(next.link));
    $('send').disabled = true;
    history.replaceState(null, '', location.pathname + location.search + ticketHash(next.link));
    // Reset the entire feed, timers, listeners, image caches and composer together.
    location.reload();
  }
  var renaming = false;
  var byCode = canCode;   // which of the two ways the dialog shows
  var brought = null;     // {code, ticket}: what a code brought, kept in case only the name has to change
  var linking = false;
  function showWay(code) {
    byCode = code && canCode;
    $('codeFields').hidden = !byCode;
    $('linkFields').hidden = byCode;
    $('useCode').hidden = !canCode;
    $('deviceError').hidden = true;
  }
  function editDevice(rename) {
    renaming = rename;
    $('deviceDialogTitle').textContent = rename ? 'Rename device' : 'Add device';
    $('deviceName').value = rename && active ? active.name : '';
    $('deviceLink').value = $('deviceCode').value = '';
    brought = null;
    showWay(true);
    $('pairFields').hidden = rename;
    $('deviceError').hidden = true;
    $('saveDevice').textContent = rename ? 'Save name' : 'Add and switch';
    $('deviceDialog').showModal();
  }
  function formError(message) {
    $('deviceError').textContent = message;
    $('deviceError').hidden = false;
  }
  $('devicePicker').onchange = function () { switchDevice(deviceById(this.value)); };
  $('addDevice').onclick = $('firstDevice').onclick = function () { editDevice(false); };
  $('renameDevice').onclick = function () { editDevice(true); };
  $('cancelDevice').onclick = function () { $('deviceDialog').close(); };

  // ------------------------------------------------------------ version history
  /* The list lives in versions.json, the one place versions are written down. */
  function showVersions(releases) {
    var list = $('versionsList');
    list.replaceChildren();
    if (!releases || !releases.length) {
      list.appendChild(element('p', '', 'The list did not load. Check the internet on this phone, then try again.'));
      return;
    }
    releases.forEach(function (release, at) {
      var running = release.version === APP_VERSION;
      var entry = element('details', running ? 'version running' : 'version');
      entry.open = running || (at === 0 && !releases.some(function (r) { return r.version === APP_VERSION; }));
      var count = release.changes.length + (release.changes.length === 1 ? ' change' : ' changes');
      var head = element('summary');
      head.appendChild(element('strong', '', release.version));
      head.appendChild(element('span', 'when', release.date));
      head.appendChild(element('span', 'what', release.title));
      head.appendChild(element('span', running ? 'mark' : 'count', running ? 'this version' : count));
      entry.appendChild(head);
      var changes = element('ul');
      release.changes.forEach(function (change) { changes.appendChild(element('li', '', change.replace(/`/g, ''))); });
      entry.appendChild(changes);
      list.appendChild(entry);
    });
  }
  $('showVersions').onclick = function () {
    $('versionsList').replaceChildren(element('p', '', 'Loading'));
    $('versionsDialog').showModal();
    fetch('versions.json?v=' + APP_VERSION, { cache: 'no-cache' })
      .then(function (reply) { return reply.ok ? reply.json() : null; })
      .then(function (data) { showVersions(data && data.releases); })
      .catch(function () { showVersions(null); });
  };
  $('closeVersions').onclick = function () { $('versionsDialog').close(); };
  $('useLink').onclick = function () { showWay(false); };
  $('useCode').onclick = function () { showWay(true); };
  $('deviceDialog').onclose = function () { $('deviceLink').value = $('deviceCode').value = ''; brought = null; };
  $('deviceForm').onsubmit = async function (event) {
    event.preventDefault();
    if (linking) return;
    var name = $('deviceName').value.trim();
    if (!name) return formError('Enter a name for this computer.');
    var ticket = renaming && active ? active.link : null;
    if (!renaming && byCode) {
      var code = $('deviceCode').value.trim(), why = '';
      if (!/^[0-9]{4}$/.test(code)) return formError('Type the 4 digits that computer shows.');
      if (!brought || brought.code !== code) {
        linking = true;
        $('saveDevice').disabled = true;
        $('saveDevice').textContent = 'Linking…';
        $('deviceError').hidden = true;
        try {
          brought = { code: code, ticket: decodeTicket(new URL(await linkByCode(code)).hash) };
          if (!brought.ticket) throw trouble('silent');
        } catch (error) {
          brought = null;
          why = codeWhy(error);
        }
        linking = false;
        $('saveDevice').disabled = false;
        $('saveDevice').textContent = 'Add and switch';
        if (!$('deviceDialog').open) { brought = null; return; }   // cancelled while the computer was asked
        if (why) return formError(why);
      }
      ticket = brought.ticket;
    } else if (!renaming) {
      try { ticket = decodeTicket(new URL($('deviceLink').value.trim()).hash); } catch (error) { ticket = null; }
      if (!ticket) return formError('That is not a Vector Relay pairing link. Copy the complete link from the QR.');
    }
    if (!renaming && ticket && location.protocol === 'https:' && !ticket.u.startsWith('https:')) return formError('This is a local-only pairing. Use a Cloudflare pairing QR for access on cellular.');
    if (!ticket) return;
    if (devices.some(function (d) { return idOf(d.link) !== idOf(ticket) && d.name.toLowerCase() === name.toLowerCase(); })) {
      return formError('Use a distinct name so you can tell the computers apart.');
    }
    var next = remember(ticket, name);
    if (!next) return formError('This pairing conflicts with a saved key. Pair the other computer on its own new channel.');
    $('deviceDialog').close();
    switchDevice(next);
  };
  window.addEventListener('hashchange', function () {
    var ticket = decodeTicket(location.hash);
    if (ticket) switchDevice(remember(ticket));
  });
  deviceLabels();
  if (!link) {
    $("none").hidden = false;
    $("feed").hidden = true;
    $("menu").hidden = true;
    $("dot").className = "dot";
    $("status").textContent = "Not linked";
    return;
  }
  $("composer").hidden = false;
  $("menu").addEventListener("click", function () { side(!document.body.classList.contains("side-open")); });
  $("shade").addEventListener("click", function () { side(false); });
  $("find").addEventListener("input", chips);
  document.addEventListener("keydown", function (event) { if (event.key === "Escape") side(false); });
  var box = $("box");
  function fit() {
    box.style.height = "auto";
    box.style.height = Math.min(box.scrollHeight + 2, window.innerHeight * 0.38) + "px";
  }
  box.addEventListener("input", fit);
  box.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); $("composer").requestSubmit(); }
  });
  $("attach").addEventListener("click", function () { $("photo").click(); });
  $("previewRemove").addEventListener("click", function () { setPhoto(null); });
  $("photo").addEventListener("change", async function () {
    var file = $("photo").files && $("photo").files[0];
    if (!file) return;
    $("attach").disabled = true;
    try { setPhoto(await shrink(file)); }
    catch (error) {
      setPhoto(null);
      $("status").textContent = "This phone could not read that photo. Try another one.";
      $("dot").className = "dot bad";
    }
    $("attach").disabled = false;
  });
  $("composer").addEventListener("submit", function (event) {
    event.preventDefault();
    var text = box.value.trim();
    if ((!text && !photo) || $("send").disabled) return;
    var prompt = { pid: randomId(), text: text || "(photo)", t: Math.floor(Date.now() / 1000), chat: selected, state: "sending" };
    if (photo) { prompt.photo = true; prompt.img = photo.data; prompt.w = photo.w; prompt.h = photo.h; prompt.url = photo.url; }
    pending.push(prompt);
    box.value = "";
    setPhoto(null);
    fit();
    shown = "";
    render();
    $("feed").scrollTop = $("feed").scrollHeight;
    deliver(prompt);
  });
  if (ios && !standalone && !store.getItem(HINT)) {
    $("hint").hidden = false;
    $("hintClose").onclick = function () { store.setItem(HINT, "1"); $("hint").hidden = true; };
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) look(); });
  window.addEventListener("online", look);
  render();
  look();
}());
