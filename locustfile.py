from locust import HttpUser, task, between

class WebResearchUser(HttpUser):
    # Har request ke beech 1 se 3 second ka wait karega (bilkul asli insaan ki tarah)
    wait_time = between(1, 3)

    @task(3) # Ye task 3 guna zyada baar chalega
    def check_health(self):
        # Health endpoint par hit karenge RPS (Requests Per Second) check karne ke liye
        self.client.get("/api/health")

    @task(1) # Ye task thoda kam chalega
    def create_research(self):
        # Research endpoint par hit karenge Rate Limiting (429 Error) test karne ke liye
        payload = {"query": "What is the future of AI?"}
        with self.client.post("/api/research", json=payload, catch_response=True) as response:
            if response.status_code == 201:
                response.success()
            elif response.status_code == 429:
                # Agar Rate limit lagta hai, toh use hum failure nahi, success manenge test ke liye
                response.success()
            else:
                response.failure(f"Failed with status code: {response.status_code}")