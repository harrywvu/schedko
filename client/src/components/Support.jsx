import React from 'react';

const faqs = [
  {
    question: 'My upload fails or stalls.',
    answer: 'Use a PDF under 10 MB and make sure the scan is readable. If the file came from a phone photo, a cleaner export usually works better.',
  },
  {
    question: 'My section or class code is not detected.',
    answer: 'Try the exact spelling or a tighter version without spaces and punctuation. For example, use `bsit2a` instead of `BSIT 2-A`.',
  },
  {
    question: 'The timetable looks incomplete.',
    answer: 'That usually means the source scan is hard to parse. Re-upload a clearer file or check whether the schedule has multiple pages for your section.',
  },
  {
    question: 'Can I replace an uploaded file?',
    answer: 'Yes. Uploading a new file replaces the current flow and lets you generate a fresh schedule.',
  },
];

const Support = () => (
  <div className="page-shell">
    <section className="page-hero">
      <div className="page-hero-copy">
        <p className="page-eyebrow">Support</p>
        <h1 className="page-title">Help when the exam file does not behave.</h1>
        <p className="page-lead">
          The app is designed for messy real-world PDFs, but scans vary. These notes cover the most
          common issues and the fastest way to fix them.
        </p>
      </div>

      <div className="page-panel">
        <h2 className="panel-title">Need a quick check?</h2>
        <ul className="panel-list">
          <li>Use a clear PDF scan, not a blurry image export.</li>
          <li>Match your class code exactly when possible.</li>
          <li>Refresh the file if the schedule source has changed.</li>
        </ul>
      </div>
    </section>

    <section className="stacked-section">
      <h2 className="section-title">Common questions</h2>
      <div className="faq-grid">
        {faqs.map((faq) => (
          <article key={faq.question} className="faq-card">
            <h3>{faq.question}</h3>
            <p>{faq.answer}</p>
          </article>
        ))}
      </div>
    </section>

    <section className="contact-banner">
      <div>
        <h2>Still stuck?</h2>
        <p>Send a message with the file name and what went wrong so the issue can be reproduced quickly.</p>
      </div>
      <a href="mailto:johnny.vu2004@gmail.com">johnny.vu2004@gmail.com</a>
    </section>
  </div>
);

export default Support;
