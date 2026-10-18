export {};

declare global {
  namespace Express {
    interface Request {
      auth?: { userId: string; sessionId: string; tokenHash: string };
    }
  }
}
