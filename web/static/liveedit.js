/*
 * Inline editing toolbar, injected into the commissioner's edit view of a paper.
 *
 * The page it runs on is rendered with editable=True, so every editable block
 * carries data-edit-key and every photo position carries data-image-slot. This
 * script does three things: track what changed, upload photos, and POST the
 * result back. The published copy is re-rendered server-side from the saved
 * JSON, so nothing this script produces is ever served to readers directly.
 */
(function () {
  "use strict";

  var root = document.getElementById("ce-config");
  if (!root) return;

  var saveUrl = root.dataset.saveUrl;
  var uploadUrl = root.dataset.uploadUrl;
  var backUrl = root.dataset.backUrl;

  var dirty = {};
  var images = {};
  var hasChanges = false;

  // --- toolbar ------------------------------------------------------------

  var bar = document.createElement("div");
  bar.className = "ce-bar";
  bar.innerHTML =
    '<span class="ce-bar-label">Click any text to edit &middot; click, drop or paste a photo</span>' +
    '<span class="ce-bar-status" id="ce-status"></span>' +
    '<button type="button" class="ce-btn" id="ce-cancel">Done</button>' +
    '<button type="button" class="ce-btn ce-btn-primary" id="ce-save">Save changes</button>';
  document.body.appendChild(bar);
  document.body.classList.add("ce-active");

  var status = document.getElementById("ce-status");

  function setStatus(text) {
    status.textContent = text || "";
  }

  function markDirty() {
    if (hasChanges) return;
    hasChanges = true;
    setStatus("Unsaved changes");
  }

  // --- text ---------------------------------------------------------------

  var blocks = document.querySelectorAll("[data-edit-key]");
  Array.prototype.forEach.call(blocks, function (el) {
    el.addEventListener("input", function () {
      dirty[el.dataset.editKey] = el.innerHTML.trim();
      markDirty();
    });
    // Enter inside a headline shouldn't create a second line in a slot that
    // only ever renders one.
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && el.className.indexOf("headline") !== -1) {
        e.preventDefault();
      }
    });
  });

  // --- photos -------------------------------------------------------------

  var picker = document.createElement("input");
  picker.type = "file";
  picker.accept = "image/*";
  picker.style.display = "none";
  document.body.appendChild(picker);

  var pendingSlot = null;
  var hoverSlot = null;
  var widths = {};
  var removed = {};

  function slotWidthPercent(el) {
    var parent = el.parentElement;
    if (!parent || !parent.offsetWidth) return null;
    var pct = (el.offsetWidth / parent.offsetWidth) * 100;
    return Math.max(10, Math.min(100, Math.round(pct)));
  }

  function slotEl(slot) {
    return document.querySelector('[data-image-slot="' + slot + '"]');
  }

  function showPhoto(slot, url) {
    var el = slotEl(slot);
    if (!el) return;
    el.innerHTML =
      '<img src="' + url + '" alt="" style="width:100%;height:auto;display:block;" />';
    addRemoveButton(el, slot);
  }

  function showEmpty(slot) {
    var el = slotEl(slot);
    if (!el) return;
    el.innerHTML = '<div class="image-slot-empty">Click, drop or paste a photo</div>';
  }

  // Every slot gets a small remove control while editing. Without it there is
  // no way back to the automatic photo once you've uploaded one.
  function addRemoveButton(el, slot) {
    if (el.querySelector(".ce-remove")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ce-remove";
    btn.title = "Remove this photo";
    btn.textContent = "\u00d7";
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      delete images[slot];
      removed[slot] = true;
      showEmpty(slot);
      markDirty();
      setStatus("Photo removed — save to apply");
    });
    el.appendChild(btn);
  }

  function uploadFile(file, slot) {
    if (!file || !slot) return;
    if (file.size > 8 * 1024 * 1024) {
      setStatus("That photo is over 8MB — try a smaller one");
      return;
    }
    if (file.type && file.type.indexOf("image/") !== 0) {
      setStatus("That isn't an image");
      return;
    }

    setStatus("Uploading photo\u2026");
    var body = new FormData();
    body.append("photo", file);

    fetch(uploadUrl, { method: "POST", body: body })
      .then(function (r) {
        if (!r.ok) throw new Error("upload failed");
        return r.json();
      })
      .then(function (data) {
        images[slot] = data.url;
        delete removed[slot];
        markDirty();
        setStatus("Photo added — save to publish it");
        // Fill the slot immediately so the layout reflows exactly as it will
        // once saved.
        showPhoto(slot, data.url);
      })
      .catch(function () {
        setStatus("Couldn't upload that photo");
      });
  }

  Array.prototype.forEach.call(
    document.querySelectorAll("[data-image-slot]"),
    function (el) {
      var slot = el.dataset.imageSlot;

      if (el.querySelector("img")) addRemoveButton(el, slot);

      el.addEventListener("mouseenter", function () { hoverSlot = slot; });
      el.addEventListener("mouseleave", function () {
        if (hoverSlot === slot) hoverSlot = null;
      });

      // Click to pick — unless the click was the tail end of a resize drag.
      el.addEventListener("click", function () {
        if (el.dataset.justResized === "1") {
          el.dataset.justResized = "";
          return;
        }
        pendingSlot = slot;
        picker.value = "";
        picker.click();
      });

      // Drag a photo straight onto the slot.
      ["dragenter", "dragover"].forEach(function (evt) {
        el.addEventListener(evt, function (e) {
          e.preventDefault();
          el.classList.add("ce-drop-target");
        });
      });
      ["dragleave", "drop"].forEach(function (evt) {
        el.addEventListener(evt, function () {
          el.classList.remove("ce-drop-target");
        });
      });
      el.addEventListener("drop", function (e) {
        e.preventDefault();
        var file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        if (file) uploadFile(file, slot);
      });

      // Native CSS resize fires no event, so watch the box instead.
      if (typeof ResizeObserver === "function") {
        var initial = el.offsetWidth;
        var observer = new ResizeObserver(function () {
          if (Math.abs(el.offsetWidth - initial) < 2) return;
          initial = el.offsetWidth;
          var pct = slotWidthPercent(el);
          if (pct === null) return;
          widths[slot] = pct;
          el.dataset.justResized = "1";
          markDirty();
          setStatus("Photo resized — save to keep it");
        });
        observer.observe(el);
      }
    }
  );

  // Paste a screenshot straight into whichever slot you're pointing at. This
  // is how most photos in this product will actually arrive — someone
  // screenshots a group chat or a stat line and pastes it.
  document.addEventListener("paste", function (e) {
    if (!hoverSlot) return;
    var items = (e.clipboardData && e.clipboardData.items) || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].type && items[i].type.indexOf("image/") === 0) {
        e.preventDefault();
        uploadFile(items[i].getAsFile(), hoverSlot);
        return;
      }
    }
  });

  picker.addEventListener("change", function () {
    uploadFile(picker.files && picker.files[0], pendingSlot);
  });

  // --- save ---------------------------------------------------------------

  document.getElementById("ce-save").addEventListener("click", function () {
    var btn = this;
    btn.disabled = true;
    setStatus("Saving…");

    fetch(saveUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        edits: dirty,
        images: images,
        widths: widths,
        removed: Object.keys(removed),
      }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("save failed");
        return r.json();
      })
      .then(function (data) {
        hasChanges = false;
        window.location.href = data.redirect || backUrl;
      })
      .catch(function () {
        btn.disabled = false;
        setStatus("Couldn't save — try again");
      });
  });

  document.getElementById("ce-cancel").addEventListener("click", function () {
    window.location.href = backUrl;
  });

  window.addEventListener("beforeunload", function (e) {
    if (!hasChanges) return;
    e.preventDefault();
    e.returnValue = "";
  });
})();
