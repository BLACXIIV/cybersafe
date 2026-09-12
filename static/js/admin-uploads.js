// Drag-and-drop wiring shared by admin upload controls (logo, Excel imports).
// onFile receives the chosen File whether picked via dialog or dropped.
function wireDropzone(inputId, dropzoneId, onFile) {
  const input = document.getElementById(inputId);
  const dropzone = document.getElementById(dropzoneId);
  if (!input || !dropzone) return;

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(function(evt) {
    dropzone.addEventListener(evt, function(e) {
      e.preventDefault();
      e.stopPropagation();
    });
  });

  ['dragenter', 'dragover'].forEach(function(evt) {
    dropzone.addEventListener(evt, function() { dropzone.classList.add('dragover'); });
  });

  ['dragleave', 'drop'].forEach(function(evt) {
    dropzone.addEventListener(evt, function(e) {
      if (evt === 'dragleave' && dropzone.contains(e.relatedTarget)) return;
      dropzone.classList.remove('dragover');
    });
  });

  dropzone.addEventListener('drop', function(e) {
    const files = e.dataTransfer.files;
    if (files && files[0]) {
      const dt = new DataTransfer();
      dt.items.add(files[0]);
      input.files = dt.files;
      if (onFile) onFile(files[0]);
    }
  });

  input.addEventListener('change', function() {
    if (input.files && input.files[0] && onFile) onFile(input.files[0]);
  });
}
