import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8001";
const PASSWORD_KEY = "evo_article_publisher_password";

function DiagonalArt() {
  // Абстрактная иллюстрация в духе фирменного стиля превью: документ слева
  // "превращается" в готовую картинку справа через диагональный срез —
  // та же визуальная идея, что и в generate-preview.
  return (
    <svg viewBox="0 0 400 260" xmlns="http://www.w3.org/2000/svg">
      <polygon className="diagonal-fill" points="0,0 230,0 150,260 0,260" />
      <line className="diagonal-path" x1="232" y1="-10" x2="150" y2="270" />
      <rect className="diagonal-fill-soft" x="40" y="60" width="110" height="10" rx="5" />
      <rect className="diagonal-fill-soft" x="40" y="84" width="140" height="10" rx="5" opacity="0.7" />
      <rect className="diagonal-fill-soft" x="40" y="108" width="90" height="10" rx="5" opacity="0.5" />
      <path className="diagonal-path" d="M 210 130 L 268 130" markerEnd="url(#arrow)" />
      <defs>
        <marker id="arrow" markerWidth="10" markerHeight="10" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="#57c3e8" />
        </marker>
      </defs>
      <rect className="diagonal-fill" x="280" y="70" width="105" height="120" rx="6" />
      <rect x="280" y="70" width="105" height="120" rx="6" fill="none" stroke="#57c3e8" strokeWidth="2" opacity="0.55" />
      <circle cx="305" cy="98" r="8" fill="#ffffff" opacity="0.85" />
      <path d="M288 168 L315 138 L333 156 L377 118 L377 178 L288 178 Z" fill="#0aa6d6" opacity="0.55" />
    </svg>
  );
}

function ProgressBar() {
  return (
    <div className="progress-bar">
      <div className="fill" />
    </div>
  );
}

export default function Home() {
  const [apiOnline, setApiOnline] = useState(null);
  const [appPassword, setAppPassword] = useState("");
  const [needsPassword, setNeedsPassword] = useState(false);
  const [passwordInput, setPasswordInput] = useState("");
  const [passwordChecking, setPasswordChecking] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [authReady, setAuthReady] = useState(false);

  const [parsed, setParsed] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef(null);

  // --- источник статьи: Google Диск (по умолчанию) или свой компьютер ---
  const [sourceTab, setSourceTab] = useState("drive"); // 'drive' | 'computer'
  const [driveStack, setDriveStack] = useState([{ id: null, name: "Статьи" }]); // null = корень из .env
  const [driveFolders, setDriveFolders] = useState([]);
  const [driveFiles, setDriveFiles] = useState([]);
  const [driveLoading, setDriveLoading] = useState(false);
  const [driveError, setDriveError] = useState("");

  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [seoTitle, setSeoTitle] = useState("");
  const [metaDescription, setMetaDescription] = useState("");
  const [keywords, setKeywords] = useState([]);
  const [keywordInput, setKeywordInput] = useState("");
  const [contentMd, setContentMd] = useState("");
  const [collection, setCollection] = useState("blogs");
  const [publishNow, setPublishNow] = useState(false);
  const [categories, setCategories] = useState([]);
  const [categoryId, setCategoryId] = useState("");
  const [imagesFound, setImagesFound] = useState(0);
  const [imageStatus, setImageStatus] = useState([]);

  const [editorTab, setEditorTab] = useState("write");

  const [publishing, setPublishing] = useState(false);
  const [publishResult, setPublishResult] = useState(null);
  const [publishError, setPublishError] = useState("");

  // --- превью-картинка ---
  const [firstImageToken, setFirstImageToken] = useState(null);
  const [coverSource, setCoverSource] = useState("first_article_image"); // 'first_article_image' | 'upload'
  const [coverUploadBase64, setCoverUploadBase64] = useState(null);
  const [coverUploadPreview, setCoverUploadPreview] = useState(null);
  const [subject, setSubject] = useState("");
  const [previewToken, setPreviewToken] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [referenceIsSet, setReferenceIsSet] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then((r) => setApiOnline(r.ok))
      .catch(() => setApiOnline(false));
    fetch(`${API_BASE}/api/reference-image-status`)
      .then((r) => r.json())
      .then((d) => setReferenceIsSet(!!d.is_set))
      .catch(() => {});

    // Пароль (если он вообще задан в .env на сервере) проверяем сразу при
    // загрузке — сохранённым в этой вкладке значением (может быть пустым,
    // тогда бэкенд либо пропустит, если APP_PASSWORD не задан, либо
    // ответит 401, и мы сразу покажем окно ввода, а не будем ждать первого
    // клика по защищённому действию.
    const saved = sessionStorage.getItem(PASSWORD_KEY) || "";
    if (saved) setAppPassword(saved);
    fetch(`${API_BASE}/api/auth/check`, {
      headers: saved ? { "X-App-Password": saved } : {},
    })
      .then((r) => {
        if (r.status === 401) {
          sessionStorage.removeItem(PASSWORD_KEY);
          setAppPassword("");
          setNeedsPassword(true);
        }
      })
      .catch(() => {})
      .finally(() => setAuthReady(true));
  }, []);

  function authHeaders(extra = {}) {
    return appPassword ? { "X-App-Password": appPassword, ...extra } : extra;
  }

  async function handlePasswordSubmit(e) {
    e.preventDefault();
    setPasswordChecking(true);
    setPasswordError("");
    try {
      const res = await fetch(`${API_BASE}/api/auth/check`, {
        headers: { "X-App-Password": passwordInput },
      });
      if (res.status === 401) {
        setPasswordError("Неверный пароль, попробуйте ещё раз.");
        return;
      }
      if (!res.ok) {
        setPasswordError(`Сервер вернул ${res.status} — попробуйте ещё раз.`);
        return;
      }
      setAppPassword(passwordInput);
      sessionStorage.setItem(PASSWORD_KEY, passwordInput);
      setNeedsPassword(false);
      setPasswordError("");
    } catch (e) {
      setPasswordError("Не удалось связаться с сервером: " + e.message);
    } finally {
      setPasswordChecking(false);
    }
  }

  function handleAuthFailure() {
    sessionStorage.removeItem(PASSWORD_KEY);
    setAppPassword("");
    setPasswordInput("");
    setNeedsPassword(true);
  }

  async function handleReferenceUpload(file) {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/api/reference-image`, {
      method: "POST", headers: authHeaders(), body: formData,
    });
    if (res.status === 401) { handleAuthFailure(); return; }
    if (res.ok) setReferenceIsSet(true);
  }

  async function loadDriveFolder(folderId) {
    setDriveLoading(true);
    setDriveError("");
    try {
      const params = folderId ? `?folder_id=${encodeURIComponent(folderId)}` : "";
      const res = await fetch(`${API_BASE}/api/drive/list${params}`, { headers: authHeaders() });
      if (res.status === 401) { handleAuthFailure(); return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Сервер вернул ${res.status}`);
      setDriveFolders(data.folders || []);
      setDriveFiles(data.files || []);
    } catch (e) {
      setDriveError(e.message);
    } finally {
      setDriveLoading(false);
    }
  }

  function openDriveFolder(folder) {
    const newStack = [...driveStack, folder];
    setDriveStack(newStack);
    loadDriveFolder(folder.id);
  }

  function goToDriveBreadcrumb(index) {
    const newStack = driveStack.slice(0, index + 1);
    setDriveStack(newStack);
    loadDriveFolder(newStack[newStack.length - 1].id);
  }

  useEffect(() => {
    // ждём, пока разрешится проверка пароля (иначе первый запрос списка
    // папок Google Диска уходит без заголовка X-App-Password и падает с
    // 401 — до перехода на другую вкладку и обратно, когда пароль уже
    // подгружен из sessionStorage)
    if (!authReady || needsPassword) return;
    if (sourceTab === "drive" && driveStack.length === 1 && driveFolders.length === 0 && driveFiles.length === 0 && !driveLoading) {
      loadDriveFolder(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceTab, authReady, needsPassword]);

  useEffect(() => {
    if (!authReady || needsPassword) return;
    fetch(`${API_BASE}/api/categories`, { headers: authHeaders() })
      .then((r) => r.json())
      .then((d) => setCategories(d.categories || []))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authReady, needsPassword]);

  async function handleDriveFileClick(file) {
    setParsing(true);
    setParseError("");
    try {
      const params = new URLSearchParams({
        file_id: file.id, filename: file.name, is_google_doc: file.is_google_doc ? "true" : "false",
      });
      const res = await fetch(`${API_BASE}/api/drive/parse-docx?${params}`, {
        method: "POST", headers: authHeaders(),
      });
      if (res.status === 401) { handleAuthFailure(); return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Сервер вернул ${res.status}`);
      if (data.error) throw new Error(data.error);
      applyParsedData(data);
    } catch (e) {
      setParseError(e.message);
    } finally {
      setParsing(false);
    }
  }

  function applyParsedData(data) {
    setTitle(data.title);
    setSlug(data.slug);
    setSeoTitle(data.seo_title);
    setMetaDescription(data.meta_description);
    setKeywords(data.keywords || []);
    setContentMd(data.content_md);
    setCollection(data.collection || "blogs");
    setPublishNow(!!data.publish_now);
    setImagesFound(data.images_found || 0);
    setImageStatus(data.image_status || []);
    setFirstImageToken(data.first_image_token || null);
    setCoverSource(data.first_image_token ? "first_article_image" : "none");
    setCoverUploadBase64(null);
    setCoverUploadPreview(null);
    setPreviewToken(null);
    setPreviewUrl(null);
    setPublishResult(null);
    setPublishError("");
    setParsed(true);
  }

  async function handleDocxFile(file) {
    if (!file) return;
    setParsing(true);
    setParseError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${API_BASE}/api/parse-docx`, {
        method: "POST",
        headers: authHeaders(),
        body: formData,
      });
      if (res.status === 401) { handleAuthFailure(); return; }
      if (!res.ok) throw new Error(`Сервер вернул ${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      applyParsedData(data);
    } catch (e) {
      setParseError(e.message);
    } finally {
      setParsing(false);
    }
  }

  function addKeyword() {
    const value = keywordInput.trim();
    if (value && !keywords.includes(value)) setKeywords([...keywords, value]);
    setKeywordInput("");
  }

  function removeKeyword(kw) {
    setKeywords(keywords.filter((k) => k !== kw));
  }

  function handleCoverFileChange(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const dataUri = reader.result;
      setCoverUploadPreview(dataUri);
      setCoverUploadBase64(dataUri.split(",", 2)[1]);
      setCoverSource("upload");
    };
    reader.readAsDataURL(file);
  }

  async function handleGeneratePreview() {
    setGenerating(true);
    setPreviewError("");
    try {
      const body = { title: seoTitle || title, subject };
      if (coverSource === "upload" && coverUploadBase64) {
        body.source_image_base64 = coverUploadBase64;
      } else if (coverSource === "first_article_image" && firstImageToken) {
        body.source_token = firstImageToken;
      }
      const res = await fetch(`${API_BASE}/api/generate-preview`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      });
      if (res.status === 401) { handleAuthFailure(); return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Сервер вернул ${res.status}`);
      setPreviewToken(data.preview_token);
      setPreviewUrl(`${API_BASE}${data.preview_url}`);
    } catch (e) {
      setPreviewError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  async function handlePublish() {
    setPublishing(true);
    setPublishError("");
    setPublishResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/publish`, {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          title, content_md: contentMd, slug, seo_title: seoTitle,
          meta_description: metaDescription, keywords, collection, publish_now: publishNow,
          cover_token: previewToken,
          category_id: categoryId || null,
        }),
      });
      if (res.status === 401) { handleAuthFailure(); return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Сервер вернул ${res.status}`);
      setPublishResult(data);
    } catch (e) {
      setPublishError(e.message);
    } finally {
      setPublishing(false);
    }
  }

  const canPublish = !!title && !!contentMd && !publishing;
  const failedImages = imageStatus.filter((s) => s.status === "error");

  return (
    <>
      {needsPassword && (
        <div className="password-overlay">
          <form className="password-card" onSubmit={handlePasswordSubmit}>
            <img src="/logo.png" alt="ЭВОЛЮЦИЯ" style={{ height: 26, marginBottom: 18 }} />
            <div className="field">
              <label>Пароль доступа к инструменту</label>
              <input
                type="password" autoFocus value={passwordInput}
                onChange={(e) => setPasswordInput(e.target.value)}
              />
            </div>
            {passwordError && <div className="status error" style={{ marginBottom: 14 }}>{passwordError}</div>}
            <button className="btn btn-primary" type="submit" style={{ width: "100%" }} disabled={passwordChecking}>
              {passwordChecking ? "Проверяю…" : "Войти"}
            </button>
          </form>
        </div>
      )}

      <div className="navbar">
        <div className="brand">
          <img src="/logo.png" alt="ЭВОЛЮЦИЯ" />
          <span className="brand-sub">Публикация статей</span>
        </div>
        <div className={`status ${apiOnline ? "online" : apiOnline === false ? "offline" : ""}`}>
          <span className="dot" />
          {apiOnline === null ? "проверка соединения" : apiOnline ? "сервер подключён" : "сервер недоступен"}
        </div>
      </div>

      {!parsed && (
        <div className="hero">
          <div className="hero-inner">
            <div>
              <div className="eyebrow">ЭВОЛЮЦИЯ · автоматизация публикаций</div>
              <h1>От документа в Google Диске до сайта — без ручной сборки</h1>
              <p>
                Выберите статью в общей таблице редакции — заголовок, картинки
                из документа и SEO-поля разложатся по нужным местам и уйдут
                на сайт одним нажатием.
              </p>
            </div>
            <div className="hero-art"><DiagonalArt /></div>
          </div>
        </div>
      )}

      <div className="shell">
        <div className="panel">
          <div className="panel-label">
            Фирменный стиль превью (настраивается один раз)
            {referenceIsSet && <span className="badge badge-published">задан</span>}
          </div>
          <p className="hint" style={{ marginTop: 0, marginBottom: 16 }}>
            Загрузи один эталонный пример превью — нейросеть будет ориентироваться
            на расположение диагонали, цвет панели и заголовка при каждой новой генерации.
          </p>
          <div className="field">
            <input type="file" accept="image/*" onChange={(e) => handleReferenceUpload(e.target.files?.[0])} />
            {referenceIsSet && (
              <img src={`${API_BASE}/api/reference-image?_=${Date.now()}`} alt="" style={{ width: "100%", maxWidth: 420, marginTop: 10, borderRadius: 4 }} />
            )}
          </div>
        </div>

        {!parsed && (
          <div className="panel reveal">
            <div className="panel-label">Загрузка статьи</div>
            <div className="tabs">
              <button className={`tab${sourceTab === "drive" ? " active" : ""}`} onClick={() => setSourceTab("drive")}>Google Диск</button>
              <button className={`tab${sourceTab === "computer" ? " active" : ""}`} onClick={() => setSourceTab("computer")}>Мой компьютер</button>
            </div>

            {sourceTab === "drive" && (
              <div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 14, fontSize: 13.5 }}>
                  {driveStack.map((crumb, i) => (
                    <span key={i}>
                      <button
                        className="btn" style={{ padding: "4px 10px", fontSize: 13 }}
                        disabled={i === driveStack.length - 1}
                        onClick={() => goToDriveBreadcrumb(i)}
                      >
                        {crumb.name}
                      </button>
                      {i < driveStack.length - 1 && <span style={{ color: "var(--slate)", margin: "0 4px" }}>/</span>}
                    </span>
                  ))}
                </div>

                {driveLoading && <ProgressBar />}
                {driveError && (
                  <div className="status error">
                    {driveError}
                    <div className="hint" style={{ marginTop: 6 }}>
                      Проверь, что в .env заданы GOOGLE_API_KEY и GOOGLE_DRIVE_ROOT_FOLDER_ID,
                      и что папка на Google Диске расшарена как "Доступно всем, у кого есть ссылка".
                    </div>
                  </div>
                )}

                {!driveLoading && !driveError && (
                  <div>
                    {driveFolders.length === 0 && driveFiles.length === 0 && (
                      <p className="hint">Здесь пусто.</p>
                    )}
                    {driveFolders.map((folder) => (
                      <div
                        key={folder.id}
                        className="dropzone" style={{ textAlign: "left", padding: "12px 16px", marginBottom: 8, cursor: "pointer" }}
                        onClick={() => openDriveFolder(folder)}
                      >
                        📁 {folder.name}
                      </div>
                    ))}
                    {driveFiles.map((file) => (
                      <div
                        key={file.id}
                        className="dropzone" style={{ textAlign: "left", padding: "12px 16px", marginBottom: 8, cursor: "pointer" }}
                        onClick={() => handleDriveFileClick(file)}
                      >
                        📄 {file.name}
                      </div>
                    ))}
                  </div>
                )}
                {parsing && <ProgressBar />}
                {parseError && <div className="status error">{parseError}</div>}
              </div>
            )}

            {sourceTab === "computer" && (
              <div>
                <div
                  className={`dropzone${dragActive ? " active" : ""}`}
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
                  onDragLeave={() => setDragActive(false)}
                  onDrop={(e) => { e.preventDefault(); setDragActive(false); handleDocxFile(e.dataTransfer.files?.[0]); }}
                >
                  {parsing ? "Разбираю документ…" : "Перетащите .docx сюда или нажмите, чтобы выбрать файл"}
                </div>
                <input
                  ref={fileInputRef} type="file" accept=".docx" style={{ display: "none" }}
                  onChange={(e) => handleDocxFile(e.target.files?.[0])}
                />
                {parsing && <ProgressBar />}
                {parseError && <div className="status error">{parseError}</div>}
              </div>
            )}
          </div>
        )}

        {parsed && (
          <div className="grid reveal">
            <div>
              <div className="panel">
                <div className="panel-label">Статья</div>
                <div className="field">
                  <label>Заголовок</label>
                  <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
                </div>
                <div className="field">
                  <label>Slug</label>
                  <div className="slug-input">
                    <span className="slug-prefix">/</span>
                    <input className="mono" type="text" value={slug} onChange={(e) => setSlug(e.target.value)} />
                  </div>
                </div>
                <div className="hint" style={{ marginTop: 0 }}>
                  Картинок найдено в документе: {imagesFound}
                </div>
              </div>

              <div className="panel">
                <div className="tabs">
                  <button className={`tab${editorTab === "write" ? " active" : ""}`} onClick={() => setEditorTab("write")}>Markdown</button>
                  <button className={`tab${editorTab === "preview" ? " active" : ""}`} onClick={() => setEditorTab("preview")}>Предпросмотр</button>
                </div>
                {editorTab === "write" ? (
                  <textarea className="md-editor" value={contentMd} onChange={(e) => setContentMd(e.target.value)} />
                ) : (
                  <div className="markdown-preview">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        img: ({ src, alt }) => <img src={src?.startsWith("/api/") ? `${API_BASE}${src}` : src} alt={alt} />,
                        table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
                      }}
                    >
                      {contentMd}
                    </ReactMarkdown>
                  </div>
                )}
                {failedImages.length > 0 && (
                  <div className="status error">
                    Не скачалось с Яндекс.Диска: {failedImages.length} фото — проверьте ссылки в тексте вручную.
                  </div>
                )}
              </div>
            </div>

            <div>
              <div className="panel">
                <div className="panel-label">SEO</div>
                <div className="field">
                  <label>SEO-title</label>
                  <input type="text" value={seoTitle} onChange={(e) => setSeoTitle(e.target.value)} />
                </div>
                <div className="field">
                  <label>Meta description</label>
                  <textarea rows={3} value={metaDescription} onChange={(e) => setMetaDescription(e.target.value)} />
                </div>
                <div className="field">
                  <label>Ключевые слова ({keywords.length})</label>
                  <div className="chips">
                    {keywords.map((kw) => (
                      <span className="chip" key={kw}>
                        {kw}
                        <button onClick={() => removeKeyword(kw)} aria-label={`Убрать ${kw}`}>×</button>
                      </span>
                    ))}
                  </div>
                  <div className="chip-add">
                    <input
                      type="text" placeholder="добавить ключ" value={keywordInput}
                      onChange={(e) => setKeywordInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addKeyword(); } }}
                    />
                    <button className="btn" onClick={addKeyword}>Добавить</button>
                  </div>
                </div>
                <p className="hint" style={{ marginTop: 0 }}>
                  Заполняются из служебного блока в документе. Если в
                  статье блока нет или он неполный — впишите вручную.
                </p>
              </div>

              <div className="panel">
                <div className="panel-label">Превью-картинка</div>
                <div className="field">
                  <label>Источник фото</label>
                  <div className="checkbox-row" style={{ marginBottom: 8 }}>
                    <input
                      type="radio" checked={coverSource === "first_article_image"}
                      disabled={!firstImageToken}
                      onChange={() => setCoverSource("first_article_image")}
                    />
                    <span>Первое фото из статьи{!firstImageToken && " (нет фото)"}</span>
                  </div>
                  <div className="checkbox-row">
                    <input type="radio" checked={coverSource === "upload"} onChange={() => setCoverSource("upload")} />
                    <span>Загрузить своё фото</span>
                  </div>
                  {coverSource === "upload" && (
                    <input type="file" accept="image/*" onChange={handleCoverFileChange} style={{ marginTop: 6 }} />
                  )}
                  <div className="checkbox-row" style={{ marginTop: 8, marginBottom: 0 }}>
                    <input type="radio" checked={coverSource === "none"} onChange={() => setCoverSource("none")} />
                    <span>Без фото (по описанию ниже)</span>
                  </div>
                </div>
                <div className="field">
                  <label>Что на фото (необязательно, для точности)</label>
                  <input type="text" placeholder="например: перчатки диэлектрические на складе" value={subject} onChange={(e) => setSubject(e.target.value)} />
                </div>
                <button className="btn btn-primary" disabled={generating} onClick={handleGeneratePreview}>
                  {generating ? "Генерирую…" : previewToken ? "Перегенерировать" : "Сгенерировать превью"}
                </button>
                {generating && <ProgressBar />}
                {previewError && <div className="status error">{previewError}</div>}
                {previewUrl && (
                  <img src={previewUrl} alt="Превью" style={{ width: "100%", borderRadius: 4, marginTop: 14 }} />
                )}
              </div>

              <div className="panel">
                <div className="panel-label">Публикация</div>
                <div className="field">
                  <label>Категория</label>
                  <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
                    <option value="">Без категории</option>
                    {categories.map((c) => (
                      <option key={c.document_id} value={c.document_id}>{c.title || `#${c.document_id}`}</option>
                    ))}
                  </select>
                </div>
                <label className="checkbox-row">
                  <input type="checkbox" checked={publishNow} onChange={(e) => setPublishNow(e.target.checked)} />
                  Опубликовать сразу (иначе — черновик)
                </label>
                <div className="actions">
                  <button className="btn btn-primary" disabled={!canPublish} onClick={handlePublish}>
                    {publishing ? "Публикую…" : "Опубликовать"}
                  </button>
                  <button className="btn" onClick={() => { setParsed(false); setPublishResult(null); }}>
                    Другая статья
                  </button>
                </div>
                {publishing && <ProgressBar />}
                {publishError && <div className="status error">{publishError}</div>}
                {publishResult && (
                  <div className="status success">
                    Готово · id {publishResult.data?.id ?? "—"} · documentId {publishResult.data?.documentId ?? "—"}
                  </div>
                )}
                <div className="hint">
                  Картинки внутри статьи грузятся в Strapi Media Library прямо при публикации.
                  Не забудьте потом проставить ссылку в колонке "Ссылка на сайте" в таблице редакции.
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}