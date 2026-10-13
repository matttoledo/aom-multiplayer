ip netns exec ns1 python3 -c "
import socket, subprocess, time
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
rx.bind((\"0.0.0.0\", 2299))
rx.settimeout(6)
subprocess.run([\"ip\",\"netns\",\"exec\",\"ns2\",\"python3\",\"-c\",\"\"\"
import socket
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
tx.sendto(bPROBE-ns2, (10.77.0.255, 2299))
tx.sendto(bunicast-ns2, (10.77.0.1, 2299))
\"\"\"])
try:
    print(\"ns1 recebeu:\", rx.recvfrom(64))
except socket.timeout:
    print(\"ns1 NAO recebeu o broadcast\")
try:
    print(\"ns1 recebeu:\", rx.recvfrom(64))
except socket.timeout:
    print(\"ns1 NAO recebeu o unicast\")
"
