package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/neurosnap/sentences/english"
	"github.com/nlpodyssey/cybertron/pkg/tasks"
	"github.com/nlpodyssey/cybertron/pkg/tasks/textencoding"
)

type EmbeddingResult struct {
	Text   string    `json:"text"`
	Vector []float64 `json:"vector"`
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "Usage: federate_embed <text_to_embed>")
		os.Exit(1)
	}

	inputText := strings.Join(os.Args[1:], " ")
	ctx := context.Background()

	// 1. Tokenize into sentences
	tokenizer, err := english.NewSentenceTokenizer(nil)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error creating tokenizer: %v\n", err)
		os.Exit(1)
	}
	sentences := tokenizer.Tokenize(inputText)

	// 2. Load Model
	modelName := "sentence-transformers/all-MiniLM-L6-v2"
	conf := &tasks.Config{
		ModelsDir:      "models",
		ModelName:      modelName,
		DownloadPolicy: tasks.DownloadMissing,
	}

	obj, err := tasks.Load[textencoding.Interface](conf)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to load model: %v\n", err)
		os.Exit(1)
	}

	var results []EmbeddingResult
	for _, s := range sentences {
		trimmed := strings.TrimSpace(s.Text)
		if trimmed == "" {
			continue
		}

		res, err := obj.Encode(ctx, trimmed, 0)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Failed to encode sentence: %v\n", err)
			continue
		}

		results = append(results, EmbeddingResult{
			Text:   trimmed,
			Vector: res.Vector.Data().F64(),
		})
	}

	// 3. Output as JSON
	json.NewEncoder(os.Stdout).Encode(results)
}
