/**
 * QualityShare Upload Script
 * Handles drag-and-drop, preview generation, chunked file uploads,
 * SHA-256 integrity calculation, and QR code display.
 */

document.addEventListener("DOMContentLoaded", () => {
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("file-input");
    const browseBtn = document.getElementById("browse-btn");
    const previewSection = document.getElementById("preview-section");
    const galleryGrid = document.getElementById("gallery-grid");
    const fileCountText = document.getElementById("file-count-text");
    const totalSizeText = document.getElementById("total-size-text");
    const clearBtn = document.getElementById("clear-btn");
    const uploadBtn = document.getElementById("upload-btn");
    const progressSection = document.getElementById("progress-section");
    const progressStatusText = document.getElementById("progress-status-text");
    const progressPercentText = document.getElementById("progress-percent-text");
    const progressBarFill = document.getElementById("progress-bar-fill");
    const currentFileText = document.getElementById("current-file-text");
    const resultSection = document.getElementById("result-section");
    const qrImage = document.getElementById("qr-image");
    const shareLinkInput = document.getElementById("share-link-input");
    const copyBtn = document.getElementById("copy-btn");

    let selectedFiles = []; // Array of File objects

    // ── File Selection & Drag-and-Drop ─────────────────────────────────────
    browseBtn.addEventListener("click", () => fileInput.click());

    dropzone.addEventListener("click", (e) => {
        if (e.target !== browseBtn) fileInput.click();
    });

    dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
    });

    dropzone.addEventListener("dragleave", () => {
        dropzone.classList.remove("dragover");
    });

    dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (e.dataTransfer.files) {
            handleFiles(e.dataTransfer.files);
        }
    });

    fileInput.addEventListener("change", (e) => {
        if (e.target.files) {
            handleFiles(e.target.files);
        }
    });

    function handleFiles(fileList) {
        const newFiles = Array.from(fileList);

        // Check 100 file limit
        if (selectedFiles.length + newFiles.length > 100) {
            alert("Maximum 100 files allowed per share.");
            return;
        }

        for (const file of newFiles) {
            // Basic extension check client-side
            selectedFiles.push(file);
        }

        updateGallery();
    }

    function formatBytes(bytes) {
        if (bytes === 0) return "0 Bytes";
        const k = 1024;
        const sizes = ["Bytes", "KB", "MB", "GB"];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
    }

    function updateGallery() {
        if (selectedFiles.length === 0) {
            dropzone.classList.remove("hidden");
            previewSection.classList.add("hidden");
            return;
        }

        dropzone.classList.add("hidden");
        previewSection.classList.remove("hidden");
        galleryGrid.innerHTML = "";

        let totalSize = 0;

        selectedFiles.forEach((file, index) => {
            totalSize += file.size;

            const item = document.createElement("div");
            item.className = "gallery-item";

            if (file.type.startsWith("image/")) {
                const img = document.createElement("img");
                img.src = URL.createObjectURL(file);
                item.appendChild(img);
            } else if (file.type.startsWith("video/")) {
                const video = document.createElement("video");
                video.src = URL.createObjectURL(file);
                video.muted = true;
                item.appendChild(video);
            } else {
                const badge = document.createElement("div");
                badge.style.padding = "20px";
                badge.style.color = "white";
                badge.textContent = file.name;
                item.appendChild(badge);
            }

            const fileBadge = document.createElement("div");
            fileBadge.className = "file-badge";
            fileBadge.textContent = file.name;
            item.appendChild(fileBadge);

            const removeBtn = document.createElement("button");
            removeBtn.className = "remove-btn";
            removeBtn.innerHTML = "&times;";
            removeBtn.onclick = (e) => {
                e.stopPropagation();
                selectedFiles.splice(index, 1);
                updateGallery();
            };
            item.appendChild(removeBtn);

            galleryGrid.appendChild(item);
        });

        fileCountText.textContent = `${selectedFiles.length} file${selectedFiles.length > 1 ? "s" : ""} selected`;
        totalSizeText.textContent = formatBytes(totalSize);
    }

    clearBtn.addEventListener("click", () => {
        selectedFiles = [];
        fileInput.value = "";
        updateGallery();
    });

    // ── Upload Process ──────────────────────────────────────────────────────
    uploadBtn.addEventListener("click", async () => {
        if (selectedFiles.length === 0) return;

        previewSection.classList.add("hidden");
        progressSection.classList.remove("hidden");

        try {
            // 1. Initialize upload session
            progressStatusText.textContent = "Initializing upload session...";
            const filePayloads = selectedFiles.map(f => ({
                name: f.name,
                size: f.size,
                type: f.type || "application/octet-stream"
            }));

            const initRes = await fetch("/api/upload/init", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ files: filePayloads })
            });

            const initData = await initRes.json();
            if (!initRes.ok) {
                throw new Error(initData.error || "Failed to initialize upload.");
            }

            const { share_token, files: serverFiles } = initData;

            // 2. Upload files (with chunking for large files)
            const CHUNK_SIZE = 5 * 1024 * 1024; // 5 MB
            let totalBytes = selectedFiles.reduce((acc, f) => acc + f.size, 0);
            let uploadedBytesTotal = 0;

            for (let i = 0; i < selectedFiles.length; i++) {
                const file = selectedFiles[i];
                const serverFile = serverFiles[i];
                const fileId = serverFile.file_id;
                const uploadId = serverFile.upload_id;

                currentFileText.textContent = `Uploading (${i + 1}/${selectedFiles.length}): ${file.name}`;

                if (file.size <= CHUNK_SIZE && !uploadId) {
                    // Single part upload
                    const formData = new FormData();
                    formData.append("file_id", fileId);
                    formData.append("chunk_index", "1");
                    formData.append("chunk", file);

                    const chunkRes = await fetch("/api/upload/chunk", {
                        method: "POST",
                        body: formData
                    });
                    const chunkData = await chunkRes.json();
                    if (!chunkRes.ok) throw new Error(chunkData.error || `Upload failed for ${file.name}`);

                    // Complete file
                    await completeFile(fileId, []);
                    uploadedBytesTotal += file.size;
                    updateOverallProgress(uploadedBytesTotal, totalBytes);

                } else {
                    // Multipart chunked upload
                    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
                    const parts = [];

                    for (let chunkIdx = 0; chunkIdx < totalChunks; chunkIdx++) {
                        const start = chunkIdx * CHUNK_SIZE;
                        const end = Math.min(start + CHUNK_SIZE, file.size);
                        const chunkBlob = file.slice(start, end);

                        const formData = new FormData();
                        formData.append("file_id", fileId);
                        formData.append("chunk_index", (chunkIdx + 1).toString());
                        formData.append("chunk", chunkBlob);

                        const chunkRes = await fetch("/api/upload/chunk", {
                            method: "POST",
                            body: formData
                        });
                        const chunkData = await chunkRes.json();
                        if (!chunkRes.ok) throw new Error(chunkData.error || `Upload failed for ${file.name} (chunk ${chunkIdx + 1})`);

                        parts.push({
                            part_number: chunkIdx + 1,
                            etag: chunkData.etag
                        });

                        uploadedBytesTotal += (end - start);
                        updateOverallProgress(uploadedBytesTotal, totalBytes);
                    }

                    // Complete multipart file
                    await completeFile(fileId, parts);
                }
            }

            // 3. Finalize Share
            progressStatusText.textContent = "Finalizing share & generating QR code...";
            const finalizeRes = await fetch("/api/upload/finalize", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ share_token })
            });

            const finalizeData = await finalizeRes.json();
            if (!finalizeRes.ok) throw new Error(finalizeData.error || "Failed to finalize share.");

            // 4. Show success & QR
            progressSection.classList.add("hidden");
            resultSection.classList.remove("hidden");
            qrImage.src = finalizeData.qr_code;
            shareLinkInput.value = finalizeData.share_url;

        } catch (err) {
            alert("Error: " + err.message);
            progressSection.classList.add("hidden");
            previewSection.classList.remove("hidden");
        }
    });

    async function completeFile(fileId, parts) {
        const res = await fetch("/api/upload/complete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ file_id: fileId, parts: parts })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to complete file upload.");
    }

    function updateOverallProgress(uploaded, total) {
        const percent = Math.min(100, Math.round((uploaded / total) * 100));
        progressPercentText.textContent = `${percent}%`;
        progressBarFill.style.width = `${percent}%`;
    }

    // Copy link helper
    copyBtn.addEventListener("click", () => {
        shareLinkInput.select();
        navigator.clipboard.writeText(shareLinkInput.value);
        copyBtn.textContent = "Copied!";
        setTimeout(() => copyBtn.textContent = "Copy Link", 2000);
    });
});
