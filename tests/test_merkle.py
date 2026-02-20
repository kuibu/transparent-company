from __future__ import annotations

from app.ledger.merkle import MerkleTree, hash_leaf_payload, verify_proof


def test_merkle_root_and_proof_stable():
    payloads = [
        {"metric_key": "a", "value": 1},
        {"metric_key": "b", "value": 2},
        {"metric_key": "c", "value": 3},
    ]
    leaves = [hash_leaf_payload(p) for p in payloads]
    tree = MerkleTree(leaves)

    assert len(tree.root) == 64

    for i, leaf in enumerate(leaves):
        proof = [node.__dict__ for node in tree.proof(i)]
        assert verify_proof(leaf, proof, tree.root)


def test_merkle_proof_tamper_fails():
    leaves = [hash_leaf_payload({"k": i}) for i in range(4)]
    tree = MerkleTree(leaves)
    proof = [node.__dict__ for node in tree.proof(0)]

    assert verify_proof(leaves[0], proof, tree.root)

    bad_leaf = "0" * 64
    assert not verify_proof(bad_leaf, proof, tree.root)

    bad_proof = proof.copy()
    bad_proof[0] = {"direction": bad_proof[0]["direction"], "hash": "f" * 64}
    assert not verify_proof(leaves[0], bad_proof, tree.root)
