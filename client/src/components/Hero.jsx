import React, { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DotLottieReact } from '@lottiefiles/dotlottie-react';
import './schedko-modal.css';


const API_BASE = import.meta.env.VITE_API_URL;

const Hero = () => {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [validationStatus, setValidationStatus] = useState(null);
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
  const progressTimerRef = useRef(null);

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

    if (progressTimerRef.current) {
      clearInterval(progressTimerRef.current);
      progressTimerRef.current = null;
    }
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

    let cancelled = false;

    const pollProgress = async () => {
      try {
        const response = await fetch(`${API_BASE}/processing/${processingHash}`);
        if (!response.ok) {
          throw new Error('Processing status unavailable');
        }

        const data = await response.json();
        if (cancelled) return;

        setProcessingInfo(data);

        if (data.status === 'ready') {
          setShowProcessingModal(false);
          setValidationStatus(true);
          setPendingFile(null);

          if (processingMode === 'busy') {
            setProcessingInfo(null);
            setProcessingHash("");
            setProcessingMode(null);
            setShowNewFilePrompt(true);
          } else {
            setShowClassCodePrompt(true);
            setProcessingHash("");
            setProcessingInfo(null);
            setProcessingMode(null);
          }
        } else if (data.status === 'failed') {
          setShowProcessingModal(false);
          setValidationStatus(false);
          setProcessingMode(null);
        }
      } catch {
        if (!cancelled) {
          setProcessingInfo((current) => current || { message: 'Waiting for progress updates...' });
        }
      }
    };

    pollProgress();
    progressTimerRef.current = setInterval(pollProgress, 2000);

    return () => {
      cancelled = true;
      if (progressTimerRef.current) {
        clearInterval(progressTimerRef.current);
        progressTimerRef.current = null;
      }
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
    setScheduleLookupStatus(null);
    setProcessingInfo(null);
    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch(`${API_BASE}/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error('Upload failed');
      }

      const data = await response.json();
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
        startProcessingPoll(data.hash, 'busy');
      } else if (data.status === 'new') {
        setValidationStatus(true);
        setShowNewFilePrompt(true);
      } else {
        setValidationStatus(true);
        setShowClassCodePrompt(true);
      }
    } catch {
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

    setProcessingHash(uploadedFileHash);
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
          }
          return;
        }

        throw new Error(data?.detail || data?.message || 'Processing failed');
      }

      startProcessingPoll(data.hash || uploadedFileHash, 'own');
    } catch {
      setValidationStatus(false);
      setShowProcessingModal(false);
      setProcessingMode(null);
    }
  };

  return (
    <div className="container mx-auto px-4 py-8 sm:py-12 md:py-16 lg:py-20">
      {/* New File Prompt Modal */}
      {showNewFilePrompt && (
        <div className="schedko-modal-overlay">
          <div className="schedko-modal">
            <h2 className="schedko-modal-title">New Exam Schedule Detected</h2>
            <p className="text-sm text-slate-600 text-center leading-6">
              It appears this is a new exam schedule and can&apos;t be found in the database.
              Process file now?
            </p>
            <p className="text-sm text-amber-700 text-center font-semibold">
              Estimated time: 4-5 minutes
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
          </div>
        </div>
      )}

      {/* Processing Modal */}
      {showProcessingModal && (
        <div className="schedko-modal-overlay">
          <div className="schedko-modal">
            <h2 className="schedko-modal-title">
              {processingInfo?.message?.includes('already being processed')
                ? 'This PDF is already being processed'
                : 'Processing Exam Schedule'}
            </h2>
            <p className="text-sm text-slate-600 text-center leading-6">
              {processingInfo?.message || 'Processing file...'}
            </p>
            <div className="w-full">
              <div className="h-3 w-full rounded-full bg-slate-200 overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-green-500 to-emerald-400 transition-all duration-500"
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
          </div>
        </div>
      )}

      {/* Class Code Prompt Modal */}
      {showClassCodePrompt && (
        <div className="schedko-modal-overlay">
          <div className="schedko-modal">
            <h2 className="schedko-modal-title">Enter Your Class Code</h2>
            <input
              type="text"
              className="schedko-modal-input"
              placeholder="e.g. MATH101"
              value={classCode}
              onChange={e => setClassCode(e.target.value)}
              autoFocus
            />
            <div className="schedko-modal-actions">
              <button
                className="schedko-modal-btn-cancel"
                onClick={resetUploadFlow}
              >Cancel</button>
              <button
                className="schedko-modal-btn-submit"
                onClick={handleFileAndClassCodeSubmission}
                disabled={!classCode.trim()}
              >Submit</button>
            </div>
            {scheduleLookupStatus === false && (
              <p className="mt-4 text-sm text-red-600 text-center">
                No matching schedule was found for that class code.
              </p>
            )}
          </div>
        </div>
      )}
      {/* Show submitted class code for testing */}
      {showClassCodeDisplay && (
        <div className="fixed left-1/2 top-8 z-50 -translate-x-1/2 bg-green-100 text-green-800 px-6 py-2 rounded shadow transition-opacity duration-700 opacity-100 animate-fadeOut">
          Submitted class code: <b>{submittedClassCode}</b>
        </div>
      )}

      <div className="flex flex-col lg:flex-row gap-8 lg:gap-12 items-center">
          {/* Left Side Container - Hero Text */}
          <div className="flex-1 flex flex-col gap-6 md:gap-8 justify-center max-w-2xl lg:max-w-none mx-auto">                <div className="text-center">                    <h1 className="font-lora text-3xl sm:text-4xl md:text-5xl lg:text-6xl xl:text-7xl font-bold text-gray-900 mb-4 md:mb-6 drop-shadow-sm">
                          Personalized Exam <span className="bg-gradient-to-r from-amber-500 via-yellow-500 to-amber-400 bg-clip-text text-transparent drop-shadow">Schedules,</span> Made <span className="bg-gradient-to-r from-green-600 via-green-500 to-emerald-500 bg-clip-text text-transparent drop-shadow">Easy</span>
                      </h1>
                      <p className="font-jost text-base sm:text-lg md:text-xl lg:text-2xl xl:text-3xl text-gray-600 max-w-xl mx-auto drop-shadow-sm">
                          Upload your university exam file, select your program and section, and instantly view a clean, customized calendar just for you.
                      </p>
                  </div>
              </div>

              {/* Right Side Container - Animation and Upload */}
              <div className="flex-1 flex flex-col items-center justify-center gap-6 md:gap-8 max-w-2xl lg:max-w-none mx-auto">                {/* Animation */}
                  <div className="w-full max-w-[280px] sm:max-w-[400px] md:max-w-[480px] lg:max-w-[520px] xl:max-w-[600px] mx-auto">
                      <DotLottieReact
                          className="w-full h-auto"
                          src="hero-lottie.json"
                          loop
                          autoplay
                      />
                  </div>

                  {/* Upload Area */}
                  <div className="w-full max-w-sm lg:max-w-md mx-auto">
                      <div
                          className={`relative border-2 border-dashed rounded-xl p-6 sm:p-8 md:p-10 lg:p-12 text-center cursor-pointer transition-all duration-300 ease-in-out
                                  ${isDragging 
                                      ? 'border-transparent bg-blue-50 before:absolute before:inset-0 before:rounded-xl before:border-2 before:border-dashed before:border-blue-500 before:animate-pulse' 
                                      : 'border-gray-300 hover:border-gray-400 hover:shadow-lg'
                                  }`}
                          onDragOver={handleDragOver}
                          onDragLeave={handleDragLeave}
                          onDrop={handleDrop}
                          onClick={() => document.getElementById('fileInput').click()}
                      >
                          <input
                              type="file"
                              id="fileInput"
                              className="hidden"
                              accept=".pdf"
                              onChange={(e) => {
                                  const file = e.target.files[0];
                                  handleFileSelect(file);
                              }}
                          />

                          {/* File Upload Content */}
                          <div className="mb-4">
                              {isUploading ? (
                                  <div className="text-center">
                                      <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
                                      <p className="text-gray-600">Uploading and validating...</p>
                                  </div>
                              ) : validationStatus === true ? (
                                  <div className="text-center">
                                      <svg className="mx-auto h-12 w-12 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"></path>
                                      </svg>
                                      <p className="text-green-600 mt-2">Valid exam schedule file!</p>
                                  </div>
                              ) : validationStatus === false ? (
                                  <div className="text-center">
                                      <svg className="mx-auto h-12 w-12 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"></path>
                                      </svg>
                                      <p className="text-red-600 mt-2">Invalid exam schedule file</p>
                                  </div>
                              ) : (
                                  <>
                                      <svg
                                          className="mx-auto h-12 w-12 text-gray-400 group-hover:text-blue-500 transition-colors duration-300"
                                          stroke="currentColor"
                                          fill="none"
                                          viewBox="0 0 48 48"
                                      >
                                          <path
                                              d="M28 8H12a4 4 0 00-4 4v20m0 0v4a4 4 0 004 4h20a4 4 0 004-4V28m0 0V12a4 4 0 00-4-4h-4m4 20H8m24 0l-8-8m0 0l-8 8m8-8v20"
                                              strokeWidth="2"
                                              strokeLinecap="round"
                                              strokeLinejoin="round"
                                          />
                                      </svg>
                                      <p className="mt-4 text-sm text-gray-600">
                                          Drag and drop your PDF here, or click to select
                                      </p>
                                      <p className="mt-1 text-xs text-gray-500">PDF up to 30MB</p>
                                  </>
                              )}
                          </div>
                      </div>
                  </div>
              </div>
          </div>
      </div>


    
  )
}

export default Hero
