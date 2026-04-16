// server.js
import express from 'express';
import multer from 'multer';
import path from 'path';
import fs from 'fs/promises';
import cors from 'cors';
import convertPdfToImage from './processes/convertPdfToImage.js';
import { recognizeText } from './processes/ocr.js';
import { normalizeClassCode } from './processes/utils.js';
import { insertExamSchedules } from './processes/dbService.js';
import { checkSchedulesInDB } from './processes/dbService.js'
import dotenv from 'dotenv';
dotenv.config();


const app = express();
app.use(cors());
app.use(express.json());

// Add this test route temporarily
app.get('/api/test-db', async (req, res) => {
  try {
    const { connect } = await import('./processes/dbService.js');
    const collection = await connect();
    const count = await collection.countDocuments();
    res.json({ success: true, message: 'DB connected', count });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, 'uploads/'),
  filename: (req, file, cb) => cb(null, `unprocessedFile${path.extname(file.originalname)}`)
});
const upload = multer({ storage });

// --- Upload and OCR Route ---
app.post('/api/upload', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ success: false, message: 'No file uploaded' });

    const classCode = normalizeClassCode(req.body.classCode);
    const pdfPath = req.file.path;
    const outputDir = path.join('server/pages');
    const outputName = path.join(outputDir, 'page');

    // Step 1: Convert PDF → images (needed to read page 1 for metadata)
    await new Promise((resolve, reject) => {
      convertPdfToImage(pdfPath, outputName, err => err ? reject(err) : resolve());
    });

    const files = await fs.readdir(outputDir);
    const pageImages = files.filter(f => f.match(/^page-\d+\.png$/)).sort();

    if (pageImages.length === 0) {
      return res.status(400).json({ success: false, message: 'No pages found in PDF after conversion' });
    }

    // Step 2: OCR page 1 only — extract metadata (examSem, academicYear)
    const firstPageText = await recognizeText(path.join(outputDir, pageImages[0]));
    const firstLine = firstPageText.split('\n')[0];
    const semMatch = firstLine.match(/midterm|finals/i);
    const examSem = semMatch ? (semMatch[0].toLowerCase().startsWith('m') ? 'm' : 'f') : null;
    const yearMatch = firstLine.match(/(20\d{2}-20\d{2})/);
    const academicYear = yearMatch ? yearMatch[1] : null;

    if (!examSem || !academicYear) {
      return res.status(400).json({ success: false, message: 'Could not extract examSem or academicYear' });
    }

    // Step 3: Query DB — return cached result immediately if found
    const existing = await checkSchedulesInDB(classCode, examSem, academicYear);
    if (existing.length > 0) {
      return res.status(200).json({
        success: true,
        message: 'Schedules already exist in DB. Skipping OCR.',
        data: existing
      });
    }

    // Step 4: Cache miss — run full OCR across all pages
    const ocrResults = [];

    for (const img of pageImages) {
      const text = await recognizeText(path.join(outputDir, img));
      const lines = text.split('\n').slice(1);
      for (const line of lines) {
        const parts = line.split(',').map(part => part.trim());
        if (parts.length !== 6) continue;
        const [classCodeRaw, course, date, time, room, college] = parts;
        const normalizedClassCode = normalizeClassCode(classCodeRaw);
        ocrResults.push({
          classCode: normalizedClassCode,
          course,
          date,
          time,
          room,
          college,
          examSem,
          academicYear
        });
      }
    }

    // Step 5: Filter to the requested classCode, persist, and respond
    const filteredResults = ocrResults.filter(obj => obj.classCode === classCode);

    if (filteredResults.length === 0) {
      return res.status(404).json({ success: false, message: 'No matching schedule data found in OCR' });
    }

    await insertExamSchedules(filteredResults);
    console.log('Filtered results inserted into the database.');

    return res.status(200).json({ success: true, message: 'OCR complete and data inserted', data: filteredResults });
  } catch (err) {
    return res.status(500).json({ success: false, message: 'Server error', error: err.message });
  }
});



// --- Get All Schedules in DB ---
app.get('/api/schedules', async (req, res) => {
  try {
    const { classCode, examSem, academicYear } = req.query;
    // If any filter is provided, use it; otherwise, get all
    let results;
    if (classCode || examSem || academicYear) {
      results = await checkSchedulesInDB(classCode, examSem, academicYear);
    } else {
      // Get all schedules
      const collection = await (await import('./processes/dbService.js')).connect();
      results = await collection.find({}).toArray();
    }
    res.json({ success: true, data: results });
  } catch (err) {
    res.status(500).json({ success: false, message: 'Failed to fetch schedules', error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));