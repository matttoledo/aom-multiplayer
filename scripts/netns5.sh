ip netns exec ns1 python3 /tmp/rx2.py &
sleep 2
ip netns exec ns2 python3 /tmp/tx2.py
wait
