const express = require('express');
const path = require('path');
const fetch = require('node-fetch');

const app = express();
app.use(express.json({ limit: '50mb' }));
app.use(express.static(__dirname));

// Proxy all /api calls to Flask (running on localhost:5000 during dev)
app.post('/api/:action', async (req, res) => {
  const url = process.env.VERCEL ? `https://${process.env.VERCEL_URL}/api/${req.params.action}` : 'http://localhost:5000/api/' + req.params.action;
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body)
    });
    const data = await response.json();
    res.json(data);
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

app.get('/api/:action', async (req, res) => {
  const url = process.env.VERCEL ? `https://${process.env.VERCEL_URL}/api/${req.params.action}` : 'http://localhost:5000/api/' + req.params.action;
  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await response.json();
    res.json(data);
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server on port ${PORT}`));

