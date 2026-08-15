import requests
import concurrent.futures
import time

# Hum load balancer (Nginx) wale port 8000 par hit kar rahe hain
URL = "http://localhost:8000/api/research"
PAYLOAD = {"query": "Explain the concept of Load Balancing and Auto-scaling"}

def send_attack(request_id):
    try:
        # Ek post request bhej rahe hain
        response = requests.post(URL, json=PAYLOAD, timeout=5)
        return request_id, response.status_code
    except Exception as e:
        return request_id, str(e)

print("🚀 Starting Load Test on Nginx & 5 Backend Containers...")
print("Target:", URL)
print("-" * 50)

# Hum ek sath 15 requests bhejenge
total_requests = 15
success_count = 0
blocked_count = 0

start_time = time.time()

# ThreadPoolExecutor ek sath multiple users banata hai
with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
    # 15 bots ek sath button dabayenge
    futures = [executor.submit(send_attack, i) for i in range(1, total_requests + 1)]
    
    for future in concurrent.futures.as_completed(futures):
        req_id, status = future.result()
        if status == 201:
            print(f"✅ Request {req_id}: Success (201 Created) - Handled by an App Container")
            success_count += 1
        elif status == 429:
            print(f"🛑 Request {req_id}: Blocked (429 Too Many Requests) - Rate Limiter Worked!")
            blocked_count += 1
        else:
            print(f"⚠️ Request {req_id}: Other Error -> {status}")

end_time = time.time()

print("-" * 50)
print("📊 Load Test Summary:")
print(f"Total Time: {end_time - start_time:.2f} seconds")
print(f"Successful Requests: {success_count} (Inki limit bachi thi)")
print(f"Blocked by Redis: {blocked_count} (Quota khatam hone par block hue)")
print("-" * 50)
print("Tip: Apne 'docker-compose' wale terminal logs dhyan se dekho. Wahan app-1, app-2 sab me load divide hua hoga!")