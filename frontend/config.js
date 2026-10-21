// Public config — safe to commit (Firebase web config is not a secret;
// the backend enforces who can actually read data).
export const API_BASE = "https://YOUR-CLOUD-RUN-URL.run.app"; // e.g. https://lifetracker-api-xxxx.us-east4.run.app

export const FIREBASE_CONFIG = {
  apiKey: "YOUR_FIREBASE_WEB_API_KEY",
  authDomain: "YOUR_PROJECT.firebaseapp.com",
  projectId: "YOUR_PROJECT",
};
