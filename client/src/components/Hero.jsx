import React, { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { startProcessingPolling } from '../utils/pollProcessing';
import { uploadPdf } from '../utils/uploadPdf';
import './schedko-modal.css';
import Modal from './Modal';


const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const Hero = () => {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [validationStatus, setValidationStatus] = useState(null);
  const [uploadError, setUploadError] = useState('');
  const [showNewFilePrompt, setShowNewFilePrompt] = useState(false);
  const [showProcessingModal, setShowProcessingModal] = useState(false);
  const [showClassCodePrompt, setShowClassCodePrompt] = useState(false);
  const [scheduleLookupStatus, setScheduleLookupStatus] = useState(null);
  const [processingInfo, setProcessingInfo] = useState(null);
  const [processingHash, setProcessingHash] = useState("");
  const [processingMode, setProcessingMode] = useState(null);
  const [classCode, setClassCode] = useState("");
  const [submittedClassCode, setSubmittedClassCode] = useState("");
  const [uploadedFileHash, setUploadedFileHash] = useState("");
  const [pendingFile, setPendingFile] = useState(null);
  const [showClassCodeDisplay, setShowClassCodeDisplay] = useState(false);
  const navigate = useNavigate();
  const progressPollRef = useRef(null);

  const clearIncompleteServerState = async (fileHash) => {
    if (!fileHash) {
      return;
    }

    try {
      await fetch(`${API_BASE}/processing/${fileHash}/cancel`, {
        method: 'POST',
      });
    } catch {
      // Best-effort cleanup. Local state is still cleared immediately.
    }
  };

  const resetUploadFlow = async (shouldClearServerState = false) => {
    const fileHashToClear = shouldClearServerState
      ? (processingMode === 'own' ? processingHash || uploadedFileHash : uploadedFileHash)
      : null;

    if (fileHashToClear) {
      void clearIncompleteServerState(fileHashToClear);
    }

    progressPollRef.current?.();
    progressPollRef.current = null;
    setShowNewFilePrompt(false);
    setShowProcessingModal(false);
    setShowClassCodePrompt(false);
    setScheduleLookupStatus(null);
    setProcessingInfo(null);
    setProcessingHash("");
    setProcessingMode(null);
    setUploadedFileHash("");
    setPendingFile(null);
    setClassCode("");
    setValidationStatus(null);
    setUploadError('');
  };

  const formatEta = (seconds) => {
    if (seconds === null || seconds === undefined) {
      return 'Calculating...';
    }

    if (seconds < 60) {
      return `~${Math.max(1, Math.round(seconds))} sec`;
    }

    const minutes = Math.ceil(seconds / 60);
    return `~${minutes} min`;
  };

  const startProcessingPoll = (fileHash, mode = 'own') => {
    setProcessingHash(fileHash);
    setProcessingMode(mode);
    setShowProcessingModal(true);
  };

  useEffect(() => {
    if (!showProcessingModal || !processingHash) {
      return undefined;
    }

    const stopPolling = startProcessingPolling(`${API_BASE}/processing/${processingHash}`, {
      onProgress: (data) => {
        setProcessingInfo(data);

        if (data.status === 'ready') {
          setShowProcessingModal(false);
          setValidationStatus(true);

          if (processingMode === 'busy') {
            setProcessingInfo(null);
            setProcessingHash("");
            setProcessingMode(null);
            setShowNewFilePrompt(true);
          } else {
            setPendingFile(null);
            setShowClassCodePrompt(true);
            setProcessingHash("");
            setProcessingInfo(null);
            setProcessingMode(null);
          }
        } else if (data.status === 'failed' || data.status === 'cancelled') {
          setShowProcessingModal(false);
          setValidationStatus(false);
          setUploadError(data.message || 'PDF processing failed. Please try again.');
          setProcessingMode(null);
        }
      },
      onRetry: (delay) => setProcessingInfo((current) => ({
        ...current,
        message: `Connection interrupted. Retrying in ${Math.ceil(delay / 1000)} seconds...`,
      })),
      onUnavailable: (message) => {
        setShowProcessingModal(false);
        setValidationStatus(false);
        setUploadError(message);
        setProcessingMode(null);
      },
    });
    progressPollRef.current = stopPolling;

    return () => {
      stopPolling();
      if (progressPollRef.current === stopPolling) progressPollRef.current = null;
    };
  }, [showProcessingModal, processingHash, processingMode]);

  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) {
      handleFileSelect(file);
    }
  };

  const handleFileUpload = async (file) => {
    if (!file) return;
    setPendingFile(file);
    setIsUploading(true);
    setValidationStatus(null);
    setUploadError('');
    setScheduleLookupStatus(null);
    setProcessingInfo(null);
    try {
      const data = await uploadPdf(`${API_BASE}/upload`, file);
      if (!data?.hash) {
        throw new Error('Missing hash');
      }

      setUploadedFileHash(data.hash);

      if (data.status === 'cached') {
        setValidationStatus(true);
        setShowClassCodePrompt(true);
      } else if (data.status === 'processing') {
        setValidationStatus(true);
        setProcessingInfo(data);
        startProcessingPoll(data.hash, 'own');
      } else if (data.status === 'new') {
        setValidationStatus(true);
        setShowNewFilePrompt(true);
      } else {
        setValidationStatus(true);
        setShowClassCodePrompt(true);
      }
    } catch (error) {
      setUploadError(error.name === 'TimeoutError'
        ? 'The upload server took too long to respond. Please try again.'
        : error instanceof TypeError
          ? 'Could not reach the upload server. Please try again.'
          : error.message || 'Upload failed. Please try again.');
      setPendingFile(null);
      setUploadedFileHash("");
      setShowNewFilePrompt(false);
      setShowProcessingModal(false);
      setShowClassCodePrompt(false);
      setValidationStatus(false);
    } finally {
      setIsUploading(false);
    }
  };

  const handleFileSelect = (file) => {
    handleFileUpload(file);
  };

  const handleFileAndClassCodeSubmission = async () => {
    if (!classCode.trim() || !uploadedFileHash) return;
    setSubmittedClassCode(classCode); // Store for display/testing
    setIsUploading(true);
    setScheduleLookupStatus(null);
    try {
      const response = await fetch(`${API_BASE}/schedule`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          hash: uploadedFileHash,
          classCode: classCode.trim(),
        }),
        signal: AbortSignal.timeout(30_000),
      });

      if (!response.ok) {
        throw new Error('Schedule lookup failed');
      }

      const data = await response.json();
      const rows = Array.isArray(data?.rows) ? data.rows : [];
      const events = Array.isArray(data?.events) ? data.events : [];

      if (rows.length > 0) {
        setShowClassCodeDisplay(true);
        setShowClassCodePrompt(false);
        // Navigate to results page with data
        navigate('/results', { state: { dbSchedules: rows, events } });
        setClassCode("");
      } else {
        setScheduleLookupStatus(false);
      }
    } catch {
      setScheduleLookupStatus(false);
    } finally {
      setIsUploading(false);
    }
    // Fade out the class code display after 4 seconds
    setTimeout(() => setShowClassCodeDisplay(false), 4000);
  };

  const handleProcessNewFile = async () => {
    if (!pendingFile || !uploadedFileHash) return;

    // Poll only after /process has accepted the job, not while it is still uploaded.
    setProcessingHash("");
    setProcessingMode('own');
    setShowNewFilePrompt(false);
    setShowProcessingModal(true);
    setProcessingInfo({
      message: 'Processing started',
      pages_done: 0,
      pages_total: null,
      progress_percent: 0,
      estimated_remaining_seconds: null,
      status: 'processing',
    });

    try {
      const formData = new FormData();
      formData.append('file', pendingFile);
      formData.append('hash', uploadedFileHash);

      const response = await fetch(`${API_BASE}/process`, {
        method: 'POST',
        body: formData,
        signal: AbortSignal.timeout(120_000),
      });

      const data = await response.json();

      if (!response.ok) {
        if (response.status === 409) {
          const detail = data?.detail || {};
          setProcessingInfo({
            message: detail.message || 'This pdf is already being processed. Please wait',
            pages_done: detail.pages_done || 0,
            pages_total: detail.pages_total || null,
            progress_percent: detail.progress_percent || 0,
            estimated_remaining_seconds: detail.estimated_remaining_seconds || null,
            status: 'processing',
          });
          if (detail.activeHash) {
            startProcessingPoll(detail.activeHash, 'busy');
            return;
          }
        }

        throw new Error(data?.detail || data?.message || 'Processing failed');
      }

      startProcessingPoll(data.hash || uploadedFileHash, 'own');
    } catch (error) {
      setValidationStatus(false);
      setUploadError(error.name === 'TimeoutError'
        ? 'The processing server took too long to respond. Re-upload the PDF to check its progress.'
        : error.message || 'Processing failed. Please try again.');
      setShowProcessingModal(false);
      setProcessingMode(null);
    }
  };

  return (
    <section className="schedule-section layout-width" aria-labelledby="hero-title">
      {/* New File Prompt Modal */}
      {showNewFilePrompt && (
        <Modal labelledBy="new-file-title" onCancel={() => resetUploadFlow(true)}>
            <h2 id="new-file-title" className="schedko-modal-title">Process this exam schedule?</h2>
            <p className="text-sm text-slate-600 text-center leading-6">
              This PDF hasn’t been processed yet. We’ll read the file so you can search for your class.
            </p>
            <p className="text-sm text-amber-700 text-center font-semibold">
              Processing time depends on the PDF size and API availability.
            </p>
            <div className="schedko-modal-actions">
              <button
                className="schedko-modal-btn-cancel"
                onClick={() => resetUploadFlow(true)}
              >
                Not Now
              </button>
              <button
                className="schedko-modal-btn-submit"
                onClick={handleProcessNewFile}
              >
                Process File
              </button>
            </div>
        </Modal>
      )}

      {/* Processing Modal */}
      {showProcessingModal && (
        <Modal labelledBy="processing-title" onCancel={() => resetUploadFlow(processingMode !== 'busy' || showNewFilePrompt)}>
            <h2 id="processing-title" className="schedko-modal-title">
              {processingInfo?.message?.includes('already being processed')
                ? 'This PDF is already being processed'
                : 'Processing Exam Schedule'}
            </h2>
            <p className="text-sm text-slate-600 text-center leading-6">
              {processingInfo?.message || 'Processing file...'}
              {processingInfo?.retry_after_seconds > 0
                ? ` Retrying in approximately ${processingInfo.retry_after_seconds} seconds.`
                : ''}
            </p>
            <div className="w-full">
              <div className="h-3 w-full rounded-full bg-slate-200 overflow-hidden">
                <div
                  className="processing-progress"
                  style={{
                    width: `${processingInfo?.progress_percent || 0}%`,
                  }}
                />
              </div>
              <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
                <span>
                  {processingInfo?.pages_done || 0}
                  {processingInfo?.pages_total ? ` / ${processingInfo.pages_total}` : ''}
                  {processingInfo?.pages_total ? ' pages' : ' pages'}
                </span>
                <span>{formatEta(processingInfo?.estimated_remaining_seconds)}</span>
              </div>
            </div>
            <div className="schedko-modal-actions">
              <button
                className="schedko-modal-btn-cancel"
                onClick={() => resetUploadFlow(processingMode !== 'busy' || showNewFilePrompt)}
              >
                Cancel
              </button>
            </div>
        </Modal>
      )}

      {/* Class Code Prompt Modal */}
      {showClassCodePrompt && (
        <Modal labelledBy="class-code-title" onCancel={() => resetUploadFlow()}>
            <h2 id="class-code-title" className="schedko-modal-title">Enter Your Class Code</h2>
            <input
              type="text"
              className="schedko-modal-input"
              aria-label="Class code"
              placeholder="e.g. BSIT2A"
              value={classCode}
              onChange={e => setClassCode(e.target.value)}
              autoFocus
            />
            <div className="schedko-modal-actions">
              <button
                className="schedko-modal-btn-cancel"
                onClick={() => resetUploadFlow()}
              >Cancel</button>
              <button
                className="schedko-modal-btn-submit"
                onClick={handleFileAndClassCodeSubmission}
                disabled={!classCode.trim() || isUploading}
              >{isUploading ? 'Finding your exams…' : 'View my schedule'}</button>
            </div>
            {scheduleLookupStatus === false && (
              <p className="mt-4 text-sm text-red-600 text-center">
                No matching schedule was found for that class code.
              </p>
            )}
        </Modal>
      )}
      {/* Show submitted class code for testing */}
      {showClassCodeDisplay && (
        <div className="fixed left-1/2 top-8 z-50 -translate-x-1/2 bg-green-100 text-green-800 px-6 py-2 rounded shadow transition-opacity duration-700 opacity-100 animate-fadeOut">
          Submitted class code: <b>{submittedClassCode}</b>
        </div>
      )}

      <div className="workspace-heading">
        <h1 id="hero-title">Your exam schedule</h1>
        <p>Upload the university PDF to find your classes and build your timetable.</p>
      </div>
      <div className="schedule-workspace">
        <aside className="workflow-sidebar" aria-label="Schedule progress">
          <ol className="workflow-steps">
            <li aria-current={showClassCodePrompt ? undefined : 'step'}>
              <span className="workflow-number" aria-hidden="true">1</span>
              <div><span className="workflow-title">Upload PDF</span><p>The official exam schedule</p></div>
            </li>
            <li aria-current={showClassCodePrompt ? 'step' : undefined}>
              <span className="workflow-number" aria-hidden="true">2</span>
              <div><span className="workflow-title">Find your class</span><p>Enter your class code</p></div>
            </li>
            <li>
              <span className="workflow-number" aria-hidden="true">3</span>
              <div><span className="workflow-title">View timetable</span><p>Review and save your exams</p></div>
            </li>
          </ol>
          <a className="workflow-help" href="/support">Help with your schedule <span aria-hidden="true">→</span></a>
        </aside>
        <section className="upload-panel" aria-labelledby="upload-title">
          <div className="upload-heading">
            <h2 id="upload-title">Upload exam schedule</h2>
            <p>Use the latest PDF provided by your university.</p>
          </div>
          <div
            className={`upload-dropzone ${isDragging ? 'is-dragging' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={(event) => { if (isUploading) event.preventDefault(); else handleDrop(event); }}
            aria-busy={isUploading}
          >
            <svg className="upload-icon" width="40" height="46" viewBox="0 0 52 58" fill="none" aria-hidden="true">
              <path d="M10 2h22l12 12v40H10z" fill="currentColor" fillOpacity=".06" stroke="currentColor" strokeWidth="1.5" />
              <path d="M32 2v12h12M20 35l7-7 7 7M27 28v16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M5 10H2v46h33" stroke="currentColor" strokeOpacity=".3" strokeWidth="1.5" />
            </svg>
            <div className="upload-status" aria-live="polite">
              <h3>{isUploading ? 'Checking your PDF…' : validationStatus === true ? 'Your PDF is ready.' : 'Drop your PDF here'}</h3>
              <p>{isUploading ? 'Uploading and validating your exam schedule.' : 'Select a file from your device to get started.'}</p>
            </div>
            <label className={`upload-button ${isUploading ? 'is-disabled' : ''}`}>
              <input type="file" accept=".pdf,application/pdf" disabled={isUploading} aria-label="Choose exam schedule PDF" onChange={(event) => {
                const file = event.target.files[0];
                handleFileSelect(file);
                event.target.value = '';
              }} />
              {isUploading ? 'Uploading…' : 'Choose PDF'}
            </label>
            <span className="upload-file-note">PDF format · Up to 30 MB</span>
          </div>
          {validationStatus === false && <p className="upload-error" role="alert">{uploadError || 'Invalid exam schedule file. Please try again.'}</p>}
          <p className="upload-note">New PDFs need to be processed first. You’ll see the progress before entering your class code.</p>
        </section>
      </div>
    </section>
  )
}

export default Hero
