/* Vector Relay on the phone: shows what the AI chats on your PC said, and takes your next prompt to them.

   Everything travels as AES-GCM ciphertext through a relay that cannot read it. The key came from
   the QR code on the PC, after the # of this page's address, which a browser sends to no server.

   The phone asks the PC a question (route "feed", "prompt", "event" or "image") by leaving it at
   the relay and waiting there for the answer. When the PC does not answer, the last sealed copy
   the relay keeps is shown instead, with what stands in the way written in the bar. */
(function () {
  "use strict";
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
  function good(found) {
    return found && /^(https:\/\/[^\/\s]+|http:\/\/(127\.0\.0\.1|localhost)(:\d+)?)$/.test(found.u) && found.c && found.k ? found : null;
  }
  var ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var standalone = navigator.standalone === true || window.matchMedia("(display-mode: standalone)").matches;

  var link = (function () {
    var found = /^#r=([A-Za-z0-9_-]+)$/.exec(location.hash), read = null, packed = "";
    if (found) {
      try { read = good(JSON.parse(new TextDecoder().decode(fromBase64(found[1])))); } catch (error) { read = null; }
      if (read) { packed = found[1]; store.setItem(KEY, JSON.stringify(read)); }
    }
    if (!read) {
      try { read = good(JSON.parse(store.getItem(KEY) || "null")); } catch (error) { read = null; }
      if (read) packed = toBase64(new TextEncoder().encode(JSON.stringify(read)));
    }
    /* An iPhone Home Screen icon has its own storage and keeps only the address it was made from,
       so in Safari on an iPhone the key stays in the address. Everywhere else it is taken out. */
    try {
      var here = location.pathname + location.search;
      if (read && ios) history.replaceState(null, "", here + "#r=" + packed);
      else if (found) history.replaceState(null, "", here);
    } catch (error) { /* the page still works */ }
    return read;
  }());

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

  var base = link ? link.u + "/c/" + link.c : "";
  var keyPromise = link ? crypto.subtle.importKey("raw", fromBase64(link.k), "AES-GCM", false, ["encrypt", "decrypt"]) : null;

  function seal(data) {
    var iv = crypto.getRandomValues(new Uint8Array(12));
    return keyPromise.then(function (key) {
      return crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, key, new TextEncoder().encode(JSON.stringify(data)));
    }).then(function (sealed) {
      var all = new Uint8Array(12 + sealed.byteLength);
      all.set(iv, 0);
      all.set(new Uint8Array(sealed), 12);
      return toBase64(all);
    });
  }
  function unseal(text) {
    var raw = fromBase64(text.trim());
    return keyPromise.then(function (key) {
      return crypto.subtle.decrypt({ name: "AES-GCM", iv: raw.slice(0, 12) }, key, raw.slice(12));
    }).then(function (plain) { return JSON.parse(new TextDecoder().decode(plain)); },
      function () { throw trouble("unreadable"); });
  }

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
    if (problem) return ["bad", problem, true];
    if (!info.pc) return ["", "Asking your PC", true];
    if (!own) return ["", "No chat is in phone mode. On the PC, type /phone in a chat.", false];
    if (!own.on) {
      var others = info.chats.some(function (c) { return c.on; });
      return ["", "This chat left phone mode." + (others ? " Pick another one above." : " On the PC, type /phone in a chat."), false];
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

  function chips() {
    var bar = $("chats");
    bar.hidden = info.chats.length < 2;
    bar.textContent = "";
    if (bar.hidden) return;
    info.chats.forEach(function (c) {
      var chip = element("button", "chip" + (c.id === selected ? " chosen" : ""));
      chip.type = "button";
      chip.setAttribute("aria-pressed", c.id === selected ? "true" : "false");
      chip.appendChild(element("span", "dot " + (c.listening ? "ok" : c.on ? "busy" : "")));
      chip.appendChild(element("span", "name", chatName(c)));
      if (unread[c.id]) {
        var fresh = element("span", "new");
        fresh.title = "Something new";
        chip.appendChild(fresh);
      }
      chip.onclick = function () { select(c.id); };
      bar.appendChild(chip);
    });
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
    $("label").textContent = own && info.chats.length < 2 && own.label ? "· " + own.label : "";
    $("send").disabled = !found[2];
    $("box").placeholder = found[2] ? "Prompt for " + (own && info.chats.length > 1 ? chatName(own) : "Vector") : "No chat to send to";
    if (mark === shown) return;
    shown = mark;
    chips();
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
    catch (failure) { return "This phone cannot reach the relay. Check its internet, or switch between Wi-Fi and mobile data."; }
    if (reply.status === 404) return "The relay has nothing for this phone yet. On the PC, type /phone in a chat. If that does not help, make a new code there and scan it.";
    if (!reply.ok) return "The relay answered with an error (" + reply.status + "). Try again in a minute.";
    try { merge(await unseal(await reply.text()), false); }
    catch (failure) { return "The key on this phone does not open what the PC sends. Scan a new code on the PC."; }
    if (error.kind === "refused") return "The relay answered with an error (" + error.status + "). Try again in a minute.";
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
    chat: "Several chats are in phone mode. Pick one above, then try again.",
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

  // ------------------------------------------------------------ start
  if (!link) {
    $("none").hidden = false;
    $("feed").hidden = true;
    $("dot").className = "dot";
    $("status").textContent = "Not linked";
    return;
  }
  $("composer").hidden = false;
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
