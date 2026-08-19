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
    '<span class="ce-bar-label">Editing &mdash; click any text to change it</span>' +
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
  var widths = {};

  function slotWidthPercent(el) {
    var parent = el.parentElement;
    if (!parent || !parent.offsetWidth) return null;
    var pct = (el.offsetWidth / parent.offsetWidth) * 100;
    return Math.max(10, Math.min(100, Math.round(pct)));
  }

  Array.prototype.forEach.call(
    document.querySelectorAll("[data-image-slot]"),
    function (el) {
      // Click anywhere in the slot to pick a photo — but not when the click
      // was the tail end of dragging the resize handle.
      el.addEventListener("click", function (e) {
        if (el.dataset.justResized === "1") {
          el.dataset.justResized = "";
          return;
        }
        pendingSlot = el.dataset.imageSlot;
        picker.value = "";
        picker.click();
      });

      // Native CSS resize fires no event, so watch the box instead.
      if (typeof ResizeObserver === "function") {
        var initial = el.offsetWidth;
        var observer = new ResizeObserver(function () {
          if (Math.abs(el.offsetWidth - initial) < 2) return;
          initial = el.offsetWidth;
          var pct = slotWidthPercent(el);
          if (pct === null) return;
          widths[el.dataset.imageSlot] = pct;
          el.dataset.justResized = "1";
          markDirty();
          setStatus("Photo resized — save to keep it");
        });
        observer.observe(el);
      }
    }
  );

  picker.addEventListener("change", function () {
    var file = picker.files && picker.files[0];
    if (!file || !pendingSlot) return;

    if (file.size > 8 * 1024 * 1024) {
      setStatus("That photo is over 8MB — try a smaller one");
      return;
    }

    var slot = pendingSlot;
    setStatus("Uploading photo…");

    var body = new FormData();
    body.append("photo", file);

    fetch(uploadUrl, { method: "POST", body: body })
      .then(function (r) {
        if (!r.ok) throw new Error("upload failed");
        return r.json();
      })
      .then(function (data) {
        images[slot] = data.url;
        markDirty();
        setStatus("Photo added — save to publish it");
        // Fill the slot straight away so the layout reflows immediately,
        // exactly as it will once saved.
        var el = document.querySelector('[data-image-slot="' + slot + '"]');
        if (el) {
          el.innerHTML =
            '<img src="' + data.url +
            '" alt="" style="width:100%;height:auto;display:block;" />';
        }
      })
      .catch(function () {
        setStatus("Couldn't upload that photo");
      });
  });

  // --- save ---------------------------------------------------------------

  document.getElementById("ce-save").addEventListener("click", function () {
    var btn = this;
    btn.disabled = true;
    setStatus("Saving…");

    fetch(saveUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edits: dirty, images: images, widths: widths }),
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
