ip netns exec ns1 python3 /tmp/rx.py &
sleep 2
ip netns exec ns2 python3 /tmp/tx.py
wait
