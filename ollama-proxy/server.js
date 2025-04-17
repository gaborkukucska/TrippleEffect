import express from 'express';
import fetch from 'node-fetch'; // Use node-fetch v3 which supports streaming well

const app = express();
const port = 3000; // Port for the proxy server
const ollamaTarget = 'http://localhost:11434'; // Your actual Ollama server address

app.use(express.json()); // Middleware to parse JSON bodies

// Proxy route for /api/chat
app.post('/api/chat', async (req, res) => {
  const targetUrl = `${ollamaTarget}/api/chat`;
  console.log(`[${new Date().toISOString()}] Proxying request to: ${targetUrl}`);
  console.log(`[${new Date().toISOString()}] Request Body:`, JSON.stringify(req.body).substring(0, 200) + '...'); // Log truncated body

  try {
    const ollamaResponse = await fetch(targetUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/x-ndjson', // Ollama streams ndjson
        // Forward any other relevant headers if needed, but keep it simple first
      },
      body: JSON.stringify(req.body), // Forward the request body
      // node-fetch handles streaming automatically
    });

    // Check if the response status indicates an error from Ollama
    if (!ollamaResponse.ok) {
      const errorBody = await ollamaResponse.text();
      console.error(`[${new Date().toISOString()}] Ollama server error (${ollamaResponse.status}): ${errorBody}`);
      res.status(ollamaResponse.status).send(errorBody);
      return;
    }

    // Set headers for the client (TrippleEffect) to indicate streaming
    res.setHeader('Content-Type', 'application/x-ndjson');
    res.setHeader('Transfer-Encoding', 'chunked');

    // Pipe the stream from Ollama directly to the client response
    ollamaResponse.body.pipe(res);

    // Optional: Log when the stream finishes or errors
    ollamaResponse.body.on('end', () => {
      console.log(`[${new Date().toISOString()}] Stream finished for request to ${targetUrl}`);
    });
    ollamaResponse.body.on('error', (err) => {
      console.error(`[${new Date().toISOString()}] Error piping stream from Ollama:`, err);
      if (!res.headersSent) {
        res.status(500).send('Proxy stream error');
      }
      res.end(); // Ensure response is closed on error
    });

  } catch (error) {
    console.error(`[${new Date().toISOString()}] Error fetching from Ollama:`, error);
    if (!res.headersSent) {
        res.status(502).send(`Proxy error: ${error.message}`); // Bad Gateway
    } else {
        res.end(); // Ensure response is closed if headers were already sent
    }
  }
});

// Basic root route for testing if the proxy is running
app.get('/', (req, res) => {
  res.send('Ollama Proxy is running!');
});

app.listen(port, () => {
  console.log(`Ollama proxy server listening on http://localhost:${port}`);
  console.log(`Forwarding requests to: ${ollamaTarget}`);
});
