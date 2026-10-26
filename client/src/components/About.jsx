import React from 'react';

const pillars = [
  {
    title: 'Fast schedule extraction',
    text: 'Turn dense university exam PDFs into a readable schedule with less manual searching.',
  },
  {
    title: 'Find your class',
    text: 'Enter your class code to find the matching entries in the uploaded schedule.',
  },
  {
    title: 'Clear calendar output',
    text: 'See each exam in a structured timetable that is easier to scan, compare, and plan around.',
  },
];

const About = () => (
  <div className="page-shell">
    <section className="page-hero">
      <div className="page-hero-copy">
        <p className="page-eyebrow">About SchedKo</p>
        <h1 className="page-title">Find your exams in the university schedule.</h1>
        <p className="page-lead">
          SchedKo reads your university’s exam PDF and filters it by class code.
          You get a timetable with your subjects, exam times, rooms, and examiners.
        </p>
      </div>

      <div className="page-panel">
        <h2 className="panel-title">What it does</h2>
        <p className="panel-text">
          Upload the official exam file, identify your section, and get a schedule view that keeps
          the important details together instead of scattering them across multiple pages.
        </p>
      </div>
    </section>

    <section className="page-grid">
      {pillars.map((pillar) => (
        <article key={pillar.title} className="info-card">
          <h3>{pillar.title}</h3>
          <p>{pillar.text}</p>
        </article>
      ))}
    </section>
  </div>
);

export default About;
